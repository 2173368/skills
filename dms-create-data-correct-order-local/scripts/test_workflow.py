#!/usr/bin/env python3
"""离线验证预检查、送审失败、状态回查和本机凭据读取。"""
import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import aliyun_rpc
import create_order
import prompts


def base(state):
    return 200, {"Success": True, "OrderBaseInfo": {"StatusCode": state, "StatusDesc": state}}


def detail(state, steps=None):
    return 200, {"Success": True, "DataCorrectOrderDetail": {
        "Status": state, "PreCheckDetail": {"TaskCheckDO": steps or []}}}


DENIED = (403, {"Code": "Forbidden", "Message": "permission denied", "RequestId": "req-denied"})


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.rpc = self.enterContext(patch.object(aliyun_rpc, "call"))
        self.sleep = self.enterContext(patch("time.sleep"))

    def test_waits_before_submitting(self):
        self.rpc.side_effect = [
            base("new"), detail("precheck"), detail("precheck_success"),
            (200, {"Success": True}), base("toaudit")]
        self.assertTrue(create_order.report_approval(12, None, object()))
        self.assertEqual([c.args[0] for c in self.rpc.call_args_list], [
            "GetOrderBaseInfo", "GetDataCorrectOrderDetail", "GetDataCorrectOrderDetail",
            "SubmitOrderApproval", "GetOrderBaseInfo"])
        self.sleep.assert_called_once()

    def test_failed_precheck_never_submits(self):
        self.rpc.side_effect = [base("new"), detail("precheck_fail", [
            {"CheckStep": "ROW_CHECK", "CheckStatus": "FAIL", "UserTip": "影响行数不符"}])]
        self.assertFalse(create_order.report_approval(12, None, object()))
        self.assertNotIn("SubmitOrderApproval", [c.args[0] for c in self.rpc.call_args_list])
        self.assertIn("影响行数不符", self.output.getvalue())

    def test_precheck_query_error_does_not_submit(self):
        self.rpc.side_effect = [base("new"), DENIED]
        self.assertFalse(create_order.report_approval(12, None, object()))
        self.assertEqual(self.rpc.call_count, 2)
        self.assertIn("permission denied", self.output.getvalue())

    def test_precheck_timeout_does_not_submit(self):
        self.rpc.return_value = detail("precheck")
        with patch("time.monotonic", side_effect=[0, 121]):
            self.assertFalse(create_order.wait_precheck(12, None, object()))
        self.assertIn("超时", self.output.getvalue())
        self.sleep.assert_not_called()

    def test_rejected_submission_is_reported_and_not_retried(self):
        self.rpc.side_effect = [base("new"), detail("precheck_success"), DENIED, base("new")]
        self.assertFalse(create_order.report_approval(12, None, object()))
        self.assertEqual([c.args[0] for c in self.rpc.call_args_list].count("SubmitOrderApproval"), 1)
        self.assertIn("Forbidden", self.output.getvalue())
        self.assertIn("req-denied", self.output.getvalue())

    def test_submission_response_unknown_but_readback_confirms_success(self):
        self.rpc.side_effect = [
            base("new"), detail("precheck_success"),
            (0, {"ErrorMessage": "network timeout"}), base("toaudit")]
        self.assertTrue(create_order.report_approval(12, None, object()))
        self.assertEqual([c.args[0] for c in self.rpc.call_args_list].count("SubmitOrderApproval"), 1)

    def test_failed_readback_does_not_report_success(self):
        self.rpc.side_effect = [
            base("new"), detail("precheck_success"), (200, {"Success": True}), DENIED]
        self.assertFalse(create_order.report_approval(12, None, object()))
        self.assertNotIn("已进入审批流程", self.output.getvalue())

    def test_existing_order_in_audit_is_not_submitted_again(self):
        self.rpc.return_value = base("toaudit")
        self.assertTrue(create_order.report_approval(12, None, object()))
        self.assertEqual([c.args[0] for c in self.rpc.call_args_list], ["GetOrderBaseInfo"])

    def test_create_success_but_approval_failed_returns_nonzero(self):
        self.rpc.side_effect = [(200, {"Success": True, "CreateOrderResult": [12]}), base("new"),
                                detail("precheck_fail")]
        self.assertNotEqual(create_order.submit({"Comment": "a", "Param": {}}, {}, object()), 0)
        self.assertEqual([c.args[0] for c in self.rpc.call_args_list].count("CreateDataCorrectOrder"), 1)

    def test_resume_mode_never_creates_an_order(self):
        self.rpc.side_effect = [detail("toaudit"), base("toaudit")]
        with patch.object(prompts, "read_credentials", return_value=object()), \
             patch.object(prompts, "confirm_submit", return_value=True), \
             patch("sys.argv", ["create_order.py", "--order-id", "12"]):
            self.assertEqual(create_order.main(), 0)
        self.assertNotIn("CreateDataCorrectOrder", [c.args[0] for c in self.rpc.call_args_list])

    def test_error_fields_support_rpc_and_dms_formats(self):
        self.assertIn("Forbidden permission denied", aliyun_rpc.describe_error(*DENIED))
        self.assertIn("NoPermission denied", aliyun_rpc.describe_error(
            403, {"ErrorCode": "NoPermission", "ErrorMessage": "denied", "RequestId": "r2"}))


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = pathlib.Path(self.directory) / "credentials.local.json"
        self.path.write_text(json.dumps({"access_key_id": "local-id",
                                        "access_key_secret": "local-secret"}), encoding="utf-8")
        self.enterContext(patch.object(prompts, "LOCAL_CREDENTIALS_FILE", self.path, create=True))
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))

    def test_local_credentials_work_without_interactive_input(self):
        with patch("builtins.input", side_effect=AssertionError("unexpected input")), \
             patch("getpass.getpass", side_effect=AssertionError("unexpected secret input")):
            result = prompts.read_credentials()
        self.assertEqual((result.access_key_id, result.access_key_secret), ("local-id", "local-secret"))
        self.assertNotIn("local-secret", self.output.getvalue())

    def test_complete_environment_pair_overrides_local_file(self):
        with patch.dict(os.environ, {prompts.ENV_KEY_ID: "env-id", prompts.ENV_KEY_SECRET: "env-secret"}):
            result = prompts.read_credentials()
        self.assertEqual((result.access_key_id, result.access_key_secret), ("env-id", "env-secret"))

    def test_partial_environment_does_not_mix_with_local_credentials(self):
        with patch.dict(os.environ, {prompts.ENV_KEY_ID: "env-id", prompts.ENV_TOKEN: ""}), \
             patch("getpass.getpass", return_value="entered-secret"):
            result = prompts.read_credentials()
        self.assertEqual((result.access_key_id, result.access_key_secret), ("env-id", "entered-secret"))

    def test_invalid_local_credentials_do_not_leak_contents(self):
        self.path.write_text('{"access_key_secret":"local-secret"}', encoding="utf-8")
        with self.assertRaises(RuntimeError) as error:
            prompts.read_credentials()
        self.assertNotIn("local-secret", str(error.exception))

    def test_dry_run_neither_loads_credentials_nor_calls_api(self):
        spec = {"database_name": "training", "db_type": "mysql", "classify": "数据订正",
                "comment": "清理指定班级未排课的课程关联并保留回滚数据", "estimate_affect_rows": 1,
                "exec_sql": "DELETE FROM class_course WHERE id='example' LIMIT 1",
                "rollback_sql": "INSERT INTO class_course(id) VALUES('example')"}
        path = pathlib.Path(self.directory) / "order.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        with patch.object(prompts, "read_credentials", side_effect=AssertionError("credentials read")), \
             patch.object(aliyun_rpc, "call", side_effect=AssertionError("network call")), \
             patch("sys.argv", ["create_order.py", "--spec", str(path), "--dry-run"]):
            self.assertEqual(create_order.main(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

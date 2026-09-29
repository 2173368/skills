#!/usr/bin/env python3
"""创建阿里云 DMS 数据变更工单：支持参数文件（AI 生成）与交互两种输入。

  python create_order.py --spec order.json            # 非交互，仍需确认
  python create_order.py --spec order.json --dry-run  # 只校验，不联网
  python create_order.py                              # 逐项交互录入
"""

import argparse
import json
import os
import sys
import time

import aliyun_rpc
import prompts
from order_spec import SpecError, build_order


def load_spec(path):
    """从文件或 stdin（path 为 '-'）读取工单规格 JSON。"""
    try:
        raw = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    except OSError as error:
        raise RuntimeError(f"无法读取规格文件：{error}") from error
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"规格文件不是合法 JSON：{error}") from error
    if not isinstance(spec, dict):
        raise RuntimeError("规格文件顶层必须是 JSON 对象")
    return spec


def filter_rows(rows, criteria):
    """按 database_filter 精确筛候选库，避免在多个同名库间靠人工挑选。"""
    if not criteria:
        return rows
    kept = []
    for row in rows:
        if all(
            str(row.get(key, "")).lower() == str(value).lower()
            for key, value in criteria.items()
        ):
            kept.append(row)
    return kept


def summarize(rows):
    return [
        {k: r.get(k) for k in ("DatabaseId", "SchemaName", "Host", "EnvType")}
        for r in rows
    ]


def resolve_target(spec, credentials, auto):
    """未给定 target 时按 database_name 查询，唯一候选自动选中，否则交互选择。"""
    if spec.get("target"):
        return spec.get("db_type", "")
    name = spec.get("database_name")
    if not name:
        raise RuntimeError("规格需提供 target 或 database_name")

    tid = spec.get("tid")
    check_mode = spec.get("skip_control_mode_check") is not True

    if check_mode:
        # SearchDatabase 只返回部分实例的库，改为遍历可提交实例找同名库，
        # 避免拿到自由操作实例上的同名库导致提交被服务端拒绝
        rows, rejected = aliyun_rpc.find_databases_by_name(name, tid, credentials)
        if not rows:
            lines = [
                f"  - DbId={r.get('DatabaseId')} 实例={r.get('InstanceAlias')} "
                f"{r.get('Host')}：{why}"
                for r, why in rejected
            ] or [f"  - 未找到名为 {name} 的库"]
            raise RuntimeError(
                f"没有找到满足 OpenAPI 提交条件的 {name} 库"
                "（需管控模式为安全协同，或已开启安全托管）：\n" + "\n".join(lines)
            )
    else:
        rows = aliyun_rpc.search_database(name, tid, credentials)
        if not rows:
            raise RuntimeError(f"没有找到数据库：{name}")

    criteria = spec.get("database_filter") or {}
    matched = filter_rows(rows, criteria)
    if criteria and not matched:
        raise RuntimeError(
            f"database_filter {criteria} 未匹配任何候选库；实际候选：{summarize(rows)}"
        )

    if len(matched) == 1:
        row = matched[0]
        print(f"\n候选库唯一，自动选中：DbId={row.get('DatabaseId')} "
              f"名称={row.get('SchemaName')} 环境={row.get('EnvType')} "
              f"主机={row.get('Host')}:{row.get('Port')}")
    elif auto:
        raise RuntimeError(
            f"匹配到 {len(matched)} 个可提交的库，无人值守模式无法自动决定。"
            f"请在规格里加 database_filter 精确指定，例如 "
            f"{{\"Host\": \"...\", \"EnvType\": \"product\"}}。"
            f"候选：{summarize(matched)}"
        )
    else:
        row = prompts.choose_row(matched)
    spec["target"] = {
        "DbId": int(row["DatabaseId"]),
        "Logic": str(row.get("Logic", "")).lower() == "true" or row.get("Logic") is True,
    }
    print("\n已选择数据库：")
    print(json.dumps(row, ensure_ascii=False, indent=2))
    return spec.get("db_type") or row.get("DbType", "")


def wait_precheck(order_id, tid, credentials, timeout=120, poll_interval=5):
    """等待异步预检查通过；失败、查询异常或超时都不送审。"""
    deadline = time.monotonic() + timeout
    while True:
        status, response = aliyun_rpc.get_data_correct_order_detail(order_id, tid, credentials)
        if response.get("Success") is not True:
            print(f"预检查查询失败：{aliyun_rpc.describe_error(status, response)}")
            return False
        detail = response.get("DataCorrectOrderDetail") or {}
        state = str(detail.get("Status", "")).lower()
        steps = aliyun_rpc._as_rows(detail.get("PreCheckDetail"), "TaskCheckDO")
        failed = [step for step in steps if step.get("CheckStatus") == "FAIL"]
        if state == "precheck_fail" or failed:
            print(f"工单 {order_id} 预检查失败，未提交审批。")
            for step in failed:
                print(f"  {step.get('CheckStep')}: {step.get('UserTip') or '检查不通过'}")
            return False
        if state == "precheck_success":
            print("预检查通过。")
            return True
        if state not in ("new", "precheck"):
            print(f"工单详情状态为 {state or '未知'}，停止送审，请回查现有工单。")
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(f"等待预检查超时，工单 {order_id} 已存在，未提交审批；请按工单号继续。")
            return False
        print(f"工单 {order_id} 正在预检查，等待完成...")
        time.sleep(min(poll_interval, remaining))


def report_approval(order_id, tid, credentials):
    """预检查通过后送审，并以实际回查状态判断是否成功。"""
    status, response = aliyun_rpc.get_order_base_info(order_id, tid, credentials)
    if response.get("Success") is not True:
        print(f"工单已创建，但状态回查失败：{aliyun_rpc.describe_error(status, response)}")
        return False
    info = response.get("OrderBaseInfo") or {}
    code = str(info.get("StatusCode", "")).upper()

    if code == "NEW":
        if not wait_precheck(order_id, tid, credentials):
            return False
        print("正在提交审批...")
        submit_status, submit_response = aliyun_rpc.submit_order_approval(order_id, tid, credentials)
        if submit_response.get("Success") is not True:
            print(f"提交审批未成功：{aliyun_rpc.describe_error(submit_status, submit_response)}")
            print("回查现有工单确认结果，不重复创建或自动重试送审。")
        status, response = aliyun_rpc.get_order_base_info(order_id, tid, credentials)
        if response.get("Success") is not True:
            print(f"送审后状态回查失败：{aliyun_rpc.describe_error(status, response)}")
            return False
        info = response.get("OrderBaseInfo") or {}
        code = str(info.get("StatusCode", "")).upper()

    state = info.get("StatusDesc") or info.get("StatusCode") or "未知"
    print(f"\n工单状态：{state}")
    if code in ("TOAUDIT", "AUDITING"):
        print("已进入审批流程，等待审批人处理。")
        return True
    if code in ("APPROVED", "WAITING", "PROCESSING", "SUCCESS"):
        print("工单已通过审批阶段，请按上述实际状态核对；本脚本不会调用执行接口。")
        return True
    if code == "NEW":
        print(f"仍处于待提交审批状态，请核对错误后用 --order-id {order_id} 继续送审。")
    else:
        print("当前工单未确认进入审批流程，请在 DMS 控制台核对。")
    return False


def submit(order, spec, credentials):
    status, response = aliyun_rpc.create_data_correct_order(order, credentials)
    print(f"\nHTTP {status}")
    print(json.dumps(response, ensure_ascii=False, indent=2))
    if response.get("Success") is not True:
        print("工单创建失败，请按 RequestId / ErrorCode / ErrorMessage 排查，不要直接重试。")
        return 2

    order_id = response.get("CreateOrderResult")
    if isinstance(order_id, dict):
        order_id = order_id.get("OrderId") or order_id.get("CreateOrderResult")
    if isinstance(order_id, list) and order_id:
        order_id = order_id[0]
    print(f"工单创建成功：{order_id}")
    return 0 if report_approval(order_id, spec.get("tid"), credentials) else 3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--spec", help="工单规格 JSON 文件路径，'-' 表示从 stdin 读取")
    source.add_argument("--order-id", type=int, help="回查并继续已创建工单的审批，不创建新工单")
    parser.add_argument("--database", help="执行数据库库名，覆盖规格里的 database_name")
    parser.add_argument("--dry-run", action="store_true", help="只校验并预览，不读凭据不联网")
    parser.add_argument("--yes", action="store_true",
                        help="跳过确认（需同时设 DMS_ALLOW_UNATTENDED=1，仅用于已审阅的自动化）")
    args = parser.parse_args()

    if args.order_id is not None and (args.order_id <= 0 or args.database or args.dry_run):
        parser.error("--order-id 必须为正整数，且不能与 --database / --dry-run 同用")
    print("阿里云 DMS 数据变更工单（凭据隐藏，支持本机私有配置）")
    unattended = args.yes and os.getenv("DMS_ALLOW_UNATTENDED") == "1"

    try:
        if args.order_id is not None:
            credentials = prompts.read_credentials()
            status, response = aliyun_rpc.get_data_correct_order_detail(args.order_id, None, credentials)
            if response.get("Success") is not True:
                raise RuntimeError(aliyun_rpc.describe_error(status, response))
            print(f"现有工单 {args.order_id} 的详情（仅继续审批，不新建）：")
            print(json.dumps(response.get("DataCorrectOrderDetail"), ensure_ascii=False, indent=2))
            if not unattended and not prompts.confirm_submit(bool(args.yes)):
                print("已取消，未提交审批。")
                return 0
            return 0 if report_approval(args.order_id, None, credentials) else 3
        spec = load_spec(args.spec) if args.spec else prompts.collect_spec_interactive()
        if args.database:
            # 库名由调用方显式指定，优先于规格文件，并清掉预设 target 以强制重新解析
            spec["database_name"] = args.database
            spec.pop("target", None)

        if args.dry_run:
            db_type = spec.get("db_type") or "mysql"
            placeholder = not spec.get("target")
            if placeholder:
                # dry-run 不联网，无法用库名解析真实 DbId，占位以便校验其余字段
                spec["target"] = {"DbId": 0, "Logic": False}
            order = build_order(spec, db_type)
            print(f"\n请求预览（不含凭据，db_type={db_type}）：")
            print(json.dumps(order, ensure_ascii=False, indent=2))
            if placeholder:
                source = "SearchDatabase 快速查询" if spec.get("skip_control_mode_check") is True \
                    else "遍历可提交实例"
                print(f"\n注意：DbId=0 为占位值，真实运行时由 database_name="
                      f"{spec.get('database_name')!r} 经{source}解析。")
            print("DRY RUN：校验通过，未读取凭据，未发送请求。")
            return 0

        credentials = prompts.read_credentials()
        db_type = resolve_target(spec, credentials, unattended)
        if not db_type:
            db_type = spec.get("db_type") or "mysql"
        order = build_order(spec, db_type)
    except SpecError as error:
        print("规格校验未通过：", file=sys.stderr)
        for problem in error.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError) as error:
        print(f"准备失败：{error}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\n已中断，未发送请求。", file=sys.stderr)
        return 1

    print("\n请求预览（不含凭据）：")
    print(json.dumps(order, ensure_ascii=False, indent=2))

    if unattended:
        print("\n已按 --yes + DMS_ALLOW_UNATTENDED=1 跳过确认，直接提交。")
    elif not prompts.confirm_submit(bool(args.yes)):
        print("已取消，未发送请求。")
        return 0

    return submit(order, spec, credentials)


if __name__ == "__main__":
    raise SystemExit(main())

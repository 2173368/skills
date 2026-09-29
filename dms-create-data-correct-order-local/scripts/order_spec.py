#!/usr/bin/env python3
"""工单参数装配与校验：把 JSON 规格转成 CreateDataCorrectOrder 请求体。"""

from sql_guard import check_sql

EXEC_MODES = ("COMMITOR", "AUTO", "LAST_AUDITOR")
SQL_TYPES = ("TEXT", "ATTACHMENT")
TEXT_SQL_LIMIT = 1024

# Comment 里出现这些词说明描述空泛，无法作为审批依据
VAGUE_COMMENTS = ("测试", "修改数据", "变更数据", "test", "临时", "改一下", "调整数据")


class SpecError(ValueError):
    """规格不合法，附带全部问题，便于一次性反馈给调用方。"""

    def __init__(self, problems):
        self.problems = problems
        super().__init__("；".join(problems))


def _utf8_size(value):
    return len(value.encode("utf-8"))


def _check_comment(comment, problems):
    if not comment or len(comment.strip()) < 15:
        problems.append("Comment 至少 15 字，需写明表名、筛选条件、修改字段和业务原因")
        return
    lowered = comment.lower()
    if any(word in lowered for word in VAGUE_COMMENTS) and len(comment.strip()) < 30:
        problems.append(f"Comment 过于空泛（命中禁用词），需具体说明本次变更：{comment}")


def _check_text_sql(label, sql, db_type, problems):
    """校验文本 SQL 的安全性与长度，返回语句条数。"""
    if not sql:
        problems.append(f"{label} 为空")
        return 0
    if _utf8_size(sql) > TEXT_SQL_LIMIT:
        problems.append(f"{label} 超过 {TEXT_SQL_LIMIT} 字节，请改用附件方式")
    statements, sql_problems = check_sql(sql, db_type)
    problems.extend(f"{label} {item}" for item in sql_problems)
    return len(statements)


def _check_target(spec, problems):
    target = spec.get("target")
    if not isinstance(target, dict):
        problems.append("缺少 target（应含 DbId 与 Logic，或由数据库名称解析得到）")
        return None
    if "DbId" not in target and "InstanceId" not in target:
        problems.append("target 必须含 DbId 或 InstanceId")
        return None
    if "DbId" in target and "InstanceId" in target:
        problems.append("target 不能同时含 DbId 和 InstanceId")
        return None
    if "DbId" in target:
        try:
            return {"DbId": int(target["DbId"]), "Logic": bool(target.get("Logic", False))}
        except (TypeError, ValueError):
            problems.append(f"DbId 必须为整数：{target.get('DbId')}")
            return None
    return {"InstanceId": target["InstanceId"]}


def _check_counts(exec_count, rollback_count, problems):
    if exec_count and rollback_count and exec_count != rollback_count:
        problems.append(
            f"回滚语句数（{rollback_count}）必须与执行语句数（{exec_count}）一致"
        )


def build_order(spec, db_type):
    """把规格字典转为工单请求体。校验不通过时抛出 SpecError（含全部问题）。"""
    problems = []
    _check_comment(spec.get("comment", ""), problems)

    rows = spec.get("estimate_affect_rows")
    if not isinstance(rows, int) or isinstance(rows, bool) or rows < 0:
        problems.append("estimate_affect_rows 必须为 >=0 的整数")

    exec_mode = str(spec.get("exec_mode", "COMMITOR")).upper()
    if exec_mode not in EXEC_MODES:
        problems.append(f"exec_mode 只能是 {'/'.join(EXEC_MODES)}，当前 {exec_mode}")

    # 服务端强制要求原因分类，缺失时返回 403「原因分类不能为空」
    if not str(spec.get("classify") or "").strip():
        problems.append("classify（原因分类）不能为空，服务端强制校验，例如「数据订正」")

    sql_type = str(spec.get("sql_type", "TEXT")).upper()
    rollback_type = str(spec.get("rollback_sql_type", "TEXT")).upper()
    for label, value in (("sql_type", sql_type), ("rollback_sql_type", rollback_type)):
        if value not in SQL_TYPES:
            problems.append(f"{label} 只能是 TEXT 或 ATTACHMENT，当前 {value}")

    target = _check_target(spec, problems)
    param = {
        "EstimateAffectRows": rows if isinstance(rows, int) else 0,
        "SqlType": sql_type,
        "ExecMode": exec_mode,
        "RollbackSqlType": rollback_type,
    }
    if target:
        param["DbItemList"] = [target]
    classify = str(spec.get("classify") or "").strip()
    if classify:
        param["Classify"] = classify

    exec_count = rollback_count = 0
    if sql_type == "TEXT":
        exec_sql = spec.get("exec_sql", "")
        exec_count = _check_text_sql("执行 SQL", exec_sql, db_type, problems)
        param["ExecSQL"] = exec_sql
    else:
        if not spec.get("attachment_name"):
            problems.append("sql_type=ATTACHMENT 时必须提供 attachment_name（上传返回的 Key）")
        if not spec.get("attachment_sql_verified") is True:
            problems.append("使用 SQL 附件时必须显式置 attachment_sql_verified=true 确认已人工核对安全性")
        param["AttachmentName"] = spec.get("attachment_name", "")
        exec_count = spec.get("attachment_statement_count") or 0

    if rollback_type == "TEXT":
        rollback_sql = spec.get("rollback_sql", "")
        rollback_count = _check_text_sql("回滚 SQL", rollback_sql, db_type, problems)
        param["RollbackSQL"] = rollback_sql
    else:
        if not spec.get("rollback_attachment_name"):
            problems.append("rollback_sql_type=ATTACHMENT 时必须提供 rollback_attachment_name")
        param["RollbackAttachmentName"] = spec.get("rollback_attachment_name", "")
        rollback_count = spec.get("rollback_attachment_statement_count") or 0

    _check_counts(exec_count, rollback_count, problems)
    if problems:
        raise SpecError(problems)

    order = {"Comment": spec["comment"].strip(), "Param": param}
    if spec.get("tid") is not None:
        order["Tid"] = spec["tid"]
    if spec.get("real_login_user_uid"):
        order["RealLoginUserUid"] = spec["real_login_user_uid"]
    return order

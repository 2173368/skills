#!/usr/bin/env python3
"""交互输入层：所有校验失败都回到重问，不终止进程。"""

import getpass
import json
import os
from pathlib import Path

from aliyun_rpc import Credentials
from sql_guard import check_sql, dialect_requires_limit


def ask(prompt, default=None, required=False):
    suffix = f" [{default}]" if default is not None else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return str(default)
        if not required:
            return ""
        print("此项必填。")


def ask_int(prompt, default=None, required=False, minimum=0):
    while True:
        value = ask(prompt, default=default, required=required)
        if not value and not required:
            return None
        try:
            number = int(value)
        except ValueError:
            print(f"请输入整数（>= {minimum}）。")
            continue
        if number < minimum:
            print(f"请输入不小于 {minimum} 的整数。")
            continue
        return number


def ask_choice(prompt, choices, default):
    allowed = {choice.upper() for choice in choices}
    while True:
        value = ask(prompt, default=default, required=True).upper()
        if value in allowed:
            return value
        print(f"可选值：{', '.join(choices)}")


def ask_bool(prompt, default=False):
    return ask_choice(prompt, ["Y", "N"], "Y" if default else "N") == "Y"


ENV_KEY_ID = "ALIBABA_CLOUD_ACCESS_KEY_ID"
ENV_KEY_SECRET = "ALIBABA_CLOUD_ACCESS_KEY_SECRET"
ENV_TOKEN = "ALIBABA_CLOUD_SECURITY_TOKEN"
LOCAL_CREDENTIALS_FILE = Path(__file__).resolve().parent.parent / "credentials.local.json"


def read_credentials():
    """优先环境变量，其次本机私有文件，最后隐藏交互输入。"""
    key_id = os.getenv(ENV_KEY_ID)
    secret = os.getenv(ENV_KEY_SECRET)
    token = os.getenv(ENV_TOKEN)

    if key_id and secret:
        print(f"已从环境变量读取凭据（{ENV_KEY_ID} / {ENV_KEY_SECRET}）。")
        return Credentials(key_id, secret, token)

    # 环境变量必须作为一整组使用，避免与本机文件拼出不匹配的凭据。
    if not key_id and not secret and LOCAL_CREDENTIALS_FILE.is_file():
        try:
            data = json.loads(LOCAL_CREDENTIALS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RuntimeError("本机 DMS 凭据文件无法读取或不是合法 JSON") from error
        if not isinstance(data, dict) or not all(
            isinstance(data.get(key), str) and data[key].strip()
            for key in ("access_key_id", "access_key_secret")
        ):
            raise RuntimeError("本机 DMS 凭据文件缺少有效的 access_key_id / access_key_secret")
        print("已读取本机私有 DMS 凭据（内容隐藏）。")
        return Credentials(data["access_key_id"], data["access_key_secret"],
                           data.get("security_token"))

    print("请输入阿里云调用凭据（交互 Secret 仅在本次进程内使用，不写入文件）。")
    print(f"提示：也可预先导出 {ENV_KEY_ID} 和 {ENV_KEY_SECRET} 实现免交互。")
    key_id = key_id or ask("AccessKey ID", required=True)
    while not secret:
        secret = getpass.getpass("AccessKey Secret（隐藏输入）: ").strip()
        if not secret:
            print("AccessKey Secret 不能为空。")
    if token is None:
        token = getpass.getpass("STS Security Token（可选，隐藏输入，回车跳过）: ").strip()
    return Credentials(key_id, secret, token)


def choose_row(rows):
    """展示数据库候选并让用户选择；序号越界时重问，不退出。"""
    print(f"\n找到 {len(rows)} 个匹配数据库：")
    for index, row in enumerate(rows, 1):
        print(
            f"{index}. DbId={row.get('DatabaseId')} 名称={row.get('SchemaName')} "
            f"Logic={row.get('Logic')} 类型={row.get('DbType')} "
            f"环境={row.get('EnvType')} 主机={row.get('Host')}:{row.get('Port')}"
        )
    while True:
        selected = ask_int("选择数据库序号", required=True, minimum=1)
        if 1 <= selected <= len(rows):
            row = rows[selected - 1]
            if row.get("DatabaseId") in (None, ""):
                print("该条目缺少 DatabaseId，请换一个。")
                continue
            return row
        print(f"序号超出范围，请输入 1-{len(rows)}。")


def ask_sql(label, db_type):
    """录入 SQL 并做安全校验，未通过则列出全部问题后重问。"""
    hint = "每条 UPDATE/DELETE 需带 WHERE 和 LIMIT 正整数" if dialect_requires_limit(db_type) \
        else "每条 UPDATE/DELETE 需带 WHERE 条件"
    while True:
        sql = ask(f"{label}（{hint}）", required=True)
        statements, problems = check_sql(sql, db_type)
        if not problems:
            return sql, len(statements)
        for problem in problems:
            print(f"  - {problem}")
        print("请修正后重新输入。")


def collect_spec_interactive():
    """交互收集工单规格，返回与 --spec 文件同结构的字典。"""
    spec = {"database_name": ask("数据库名称（支持模糊搜索）", required=True)}
    spec["tid"] = ask_int("租户 ID Tid（可选，回车跳过）")
    db_type = ask("数据库类型（用于判定是否强制 LIMIT，如 mysql/PostgreSQL）", default="mysql")
    spec["db_type"] = db_type

    spec["comment"] = ask(
        "更新描述 Comment（写明表名、筛选条件、修改字段和业务原因，至少 15 字）", required=True
    )
    # 服务端强制要求，缺失会在提交时返回 403
    spec["classify"] = ask("原因分类 Classify（必填，如 数据订正）", default="数据订正")
    spec["estimate_affect_rows"] = ask_int("预估影响行数", default=0, required=True)
    spec["exec_mode"] = ask_choice(
        "执行方式", ["COMMITOR", "AUTO", "LAST_AUDITOR"], "COMMITOR"
    )

    spec["sql_type"] = ask_choice("SQL 提交方式", ["TEXT", "ATTACHMENT"], "TEXT")
    exec_count = 0
    if spec["sql_type"] == "TEXT":
        spec["exec_sql"], exec_count = ask_sql("执行 SQL", db_type)
    else:
        spec["attachment_name"] = ask("执行 SQL 附件 Key", required=True)
        spec["attachment_sql_verified"] = ask_bool(
            "确认已人工核对附件内每条 SQL 的 WHERE/LIMIT 安全性", default=False
        )
        spec["attachment_statement_count"] = ask_int("附件内执行 SQL 条数", required=True, minimum=1)
        exec_count = spec["attachment_statement_count"]

    print(f"需为每条执行 SQL 提供对应回滚语句（共 {exec_count} 条）。")
    spec["rollback_sql_type"] = ask_choice("回滚 SQL 提交方式", ["TEXT", "ATTACHMENT"], "TEXT")
    if spec["rollback_sql_type"] == "TEXT":
        spec["rollback_sql"], _ = ask_sql("回滚 SQL（基于真实原值生成）", db_type)
    else:
        spec["rollback_attachment_name"] = ask("回滚 SQL 附件 Key", required=True)
        spec["rollback_attachment_statement_count"] = ask_int(
            "回滚附件内 SQL 条数", required=True, minimum=1
        )

    uid = ask("实际调用用户 UID（可选）")
    if uid:
        spec["real_login_user_uid"] = uid
    return spec


def confirm_submit(yes_flag_given):
    if yes_flag_given:
        print("\n检测到 --yes 但未设 DMS_ALLOW_UNATTENDED=1，仍需人工确认。")
    try:
        return input("\n确认创建真实工单请输入 YES: ").strip() == "YES"
    except (EOFError, KeyboardInterrupt):
        # 无人值守却未显式放行，按拒绝处理，避免误建工单
        print("\n未能读取确认输入，按取消处理。")
        return False

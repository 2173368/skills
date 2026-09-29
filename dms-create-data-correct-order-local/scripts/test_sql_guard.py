#!/usr/bin/env python3
"""sql_guard 回归测试：覆盖旧版正则校验的全部误判场景。"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from sql_guard import check_sql, dialect_requires_limit, split_statements

FAILED = []


def expect(name, actual, wanted):
    ok = actual == wanted
    if not ok:
        FAILED.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  期望={wanted} 实际={actual}"))


def accepts(name, sql, db_type, wanted_count=1):
    statements, problems = check_sql(sql, db_type)
    expect(name, (len(statements), problems), (wanted_count, []))


def rejects(name, sql, db_type):
    _, problems = check_sql(sql, db_type)
    expect(name, bool(problems), True)


# --- 语句拆分：字符串和注释内的分号不是分隔符 ---
expect(
    "字符串内分号不拆分",
    split_statements("UPDATE t SET a='x;y' WHERE id=1 LIMIT 1"),
    ["UPDATE t SET a='x;y' WHERE id=1 LIMIT 1"],
)
expect(
    "反引号内分号不拆分",
    len(split_statements("UPDATE `t;x` SET a=1 WHERE id=1 LIMIT 1")),
    1,
)
expect(
    "注释内分号不拆分",
    len(split_statements("UPDATE t SET a=1 WHERE id=1 LIMIT 1 -- a;b")),
    1,
)
expect(
    "真实多语句正常拆分",
    len(split_statements("UPDATE t SET a=1 WHERE id=1 LIMIT 1; UPDATE t SET a=2 WHERE id=2 LIMIT 1;")),
    2,
)

# --- 旧版放行但必须拒绝：LIMIT 藏在字面量或注释里 ---
rejects("字面量里的LIMIT不算", "UPDATE t SET note='LIMIT 100' WHERE id=1", "mysql")
rejects("行注释里的LIMIT不算", "UPDATE t SET a=1 WHERE id=2 -- LIMIT 5", "mysql")
rejects("井号注释里的LIMIT不算", "UPDATE t SET a=1 WHERE id=2 # LIMIT 5", "mysql")
rejects("块注释里的LIMIT不算", "UPDATE t SET a=1 /* LIMIT 5 */ WHERE id=3", "mysql")
rejects("双写引号内的LIMIT不算", "UPDATE t SET note='it''s LIMIT 9' WHERE id=1", "mysql")

# --- 旧版拒绝但必须放行 ---
accepts("回滚用INSERT无需LIMIT", "INSERT INTO t(id,a) VALUES(1,'x')", "mysql")
accepts("回滚用多值INSERT", "INSERT INTO t(id,a) VALUES(1,'a'),(2,'b')", "mysql")
accepts("PG的UPDATE不支持LIMIT", "UPDATE t SET a=1 WHERE id=3", "PostgreSQL")
accepts("SQLServer的DELETE不支持LIMIT", "DELETE FROM t WHERE id=3", "SQLServer")

# --- 通用安全底线：UPDATE/DELETE 必须有 WHERE ---
rejects("PG缺WHERE仍拒绝", "UPDATE t SET a=1", "PostgreSQL")
rejects("MySQL缺WHERE仍拒绝", "UPDATE t SET a=1 LIMIT 1", "mysql")
rejects("DELETE缺WHERE仍拒绝", "DELETE FROM t", "PostgreSQL")

# --- MySQL 系的 LIMIT 规则 ---
accepts("MySQL带WHERE和LIMIT", "UPDATE t SET a=1 WHERE id=4 LIMIT 1", "mysql")
accepts("转义引号内分号", "UPDATE t SET a='it\\'s;ok' WHERE id=1 LIMIT 1", "mysql")
rejects("MySQL缺LIMIT", "DELETE FROM t WHERE id=6", "mysql")
rejects("LIMIT 0无意义", "UPDATE t SET a=1 WHERE id=5 LIMIT 0", "mysql")
accepts(
    "多条各自合规",
    "UPDATE t SET a=1 WHERE id=1 LIMIT 1; DELETE FROM t WHERE id=2 LIMIT 1",
    "mysql",
    wanted_count=2,
)
rejects(
    "多条中一条违规即拒绝",
    "UPDATE t SET a=1 WHERE id=1 LIMIT 1; UPDATE t SET a=2 WHERE id=2",
    "mysql",
)

# --- 空输入与方言判定 ---
rejects("空SQL拒绝", "   ", "mysql")
rejects("只有注释拒绝", "-- nothing here", "mysql")
expect("mysql需要LIMIT", dialect_requires_limit("mysql"), True)
expect("polardb_mysql需要LIMIT", dialect_requires_limit("PolarDB_MySQL"), True)
expect("PostgreSQL不需要LIMIT", dialect_requires_limit("PostgreSQL"), False)
expect("未知方言不强制LIMIT", dialect_requires_limit("unknown_db"), False)

print(f"\n失败 {len(FAILED)} 项" + (f"：{FAILED}" if FAILED else ""))
sys.exit(1 if FAILED else 0)

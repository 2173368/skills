#!/usr/bin/env python3
"""SQL 安全校验：剥离字面量与注释后判定 WHERE / LIMIT，按方言区分规则。"""

import re

# UPDATE/DELETE 支持 LIMIT 子句的方言（MySQL 系）
_LIMIT_DIALECTS = ("mysql", "mariadb", "oceanbase", "tidb", "adb_mysql", "polardb")

_DML_NEEDS_WHERE = ("UPDATE", "DELETE")


def dialect_requires_limit(db_type):
    """仅 MySQL 系方言的 UPDATE/DELETE 支持并强制 LIMIT。"""
    name = (db_type or "").strip().lower().replace("-", "_")
    return any(key in name for key in _LIMIT_DIALECTS)


def _mask(sql):
    """把字符串字面量、标识符引用和注释替换成等长空格，保留位置与分号结构。"""
    out = []
    i, size = 0, len(sql)
    while i < size:
        char = sql[i]
        two = sql[i : i + 2]
        if two == "--" or char == "#":
            end = sql.find("\n", i)
            end = size if end == -1 else end
            out.append(" " * (end - i))
            i = end
        elif two == "/*":
            end = sql.find("*/", i + 2)
            end = size if end == -1 else end + 2
            out.append(" " * (end - i))
            i = end
        elif char in ("'", '"', "`"):
            j = i + 1
            while j < size:
                if sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == char:
                    if sql[j + 1 : j + 2] == char:  # SQL 标准的双写转义
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append(" " * (min(j, size) - i))
            i = min(j, size)
        else:
            out.append(char)
            i += 1
    return "".join(out)


def split_statements(sql):
    """按分号拆分语句，忽略字面量和注释内的分号。返回原文片段。"""
    masked = _mask(sql)
    statements, start = [], 0
    for index, char in enumerate(masked):
        if char == ";":
            piece = sql[start:index].strip()
            if _mask(piece).strip():
                statements.append(piece)
            start = index + 1
    tail = sql[start:].strip()
    if _mask(tail).strip():
        statements.append(tail)
    return statements


def _first_keyword(masked):
    match = re.match(r"\s*([A-Za-z_]+)", masked)
    return match.group(1).upper() if match else ""


def check_statement(statement, db_type):
    """校验单条语句，返回问题描述列表（空列表表示通过）。"""
    masked = _mask(statement)
    keyword = _first_keyword(masked)
    problems = []

    if keyword in _DML_NEEDS_WHERE:
        if not re.search(r"\bWHERE\b", masked, re.IGNORECASE):
            problems.append(f"{keyword} 缺少 WHERE 条件，会命中全表")
        if dialect_requires_limit(db_type):
            match = re.search(r"\bLIMIT\s+(\d+)\b", masked, re.IGNORECASE)
            if not match:
                problems.append(f"{keyword} 缺少 LIMIT 正整数（{db_type} 支持该子句）")
            elif int(match.group(1)) <= 0:
                problems.append("LIMIT 必须为正整数")
    return problems


def check_sql(sql, db_type):
    """校验整段 SQL。返回 (语句列表, 问题描述列表)。"""
    statements = split_statements(sql or "")
    if not statements:
        return [], ["SQL 为空或只包含注释"]
    problems = []
    for index, statement in enumerate(statements, 1):
        for problem in check_statement(statement, db_type):
            problems.append(f"第 {index} 条：{problem}")
    return statements, problems

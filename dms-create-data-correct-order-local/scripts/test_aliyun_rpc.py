#!/usr/bin/env python3
"""aliyun_rpc 回归测试：列表接口失败必须抛错，不得伪装成「无数据」。"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import aliyun_rpc

FAILED = []


def expect(name, actual, wanted):
    ok = actual == wanted
    if not ok:
        FAILED.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  期望={wanted} 实际={actual}"))


def raises(name, fn, keyword):
    """断言 fn 抛出 RuntimeError 且信息含 keyword。"""
    try:
        fn()
    except RuntimeError as error:
        expect(name, keyword in str(error), True)
        return
    FAILED.append(name)
    print(f"FAIL  {name}  期望抛出 RuntimeError（含 {keyword!r}），实际正常返回")


def fake_call(responses):
    """按调用顺序返回预设响应；耗尽后重复最后一个。"""
    log = []

    def call(action, parameters, credentials, method="GET", timeout=30):
        log.append((action, parameters.get("InstanceSource"), parameters.get("PageNumber")))
        index = min(len(log) - 1, len(responses) - 1)
        return responses[index]

    call.log = log
    return call


DENIED = (403, {"Success": False, "ErrorCode": "NoPermission",
                "ErrorMessage": "no permission", "RequestId": "r-1"})
CREDS = object()


def with_call(stub, fn):
    """临时替换模块级 call，保证测试后恢复。"""
    original = aliyun_rpc.call
    aliyun_rpc.call = stub
    try:
        return fn()
    finally:
        aliyun_rpc.call = original


# --- 权限不足必须抛错，而非返回空列表 ---
raises(
    "ListInstances 全失败时抛错",
    lambda: with_call(fake_call([DENIED]), lambda: aliyun_rpc.list_instances(None, CREDS)),
    "dms:ListInstances",
)
raises(
    "ListDatabases 失败时抛错",
    lambda: with_call(
        fake_call([DENIED]),
        lambda: aliyun_rpc.list_databases_by_instance("i-1", None, CREDS),
    ),
    "dms:ListDatabases",
)

# --- 正常空结果不应抛错 ---
EMPTY = (200, {"Success": True, "InstanceList": {"Instance": []}})
expect(
    "实例列表真为空时返回空列表",
    with_call(fake_call([EMPTY]), lambda: aliyun_rpc.list_instances(None, CREDS)),
    [],
)
EMPTY_DB = (200, {"Success": True, "DatabaseList": {"Database": []}})
expect(
    "数据库列表真为空时返回空列表",
    with_call(
        fake_call([EMPTY_DB]),
        lambda: aliyun_rpc.list_databases_by_instance("i-1", None, CREDS),
    ),
    [],
)

# --- 去重：同一实例出现在多个 InstanceSource 只保留一次 ---
DUP = (200, {"Success": True, "InstanceList": {"Instance": [{"InstanceId": "i-1"}]}})
expect(
    "跨 InstanceSource 按 InstanceId 去重",
    len(with_call(fake_call([DUP]), lambda: aliyun_rpc.list_instances(None, CREDS))),
    1,
)

# --- 管控模式判定 ---
expect(
    "自由操作且未托管不可提交",
    aliyun_rpc.instance_submittable(
        {"StandardGroup": {"GroupMode": "NONE_CONTROL", "GroupName": "自由操作"}}
    )[0],
    False,
)
expect(
    "安全协同可提交",
    aliyun_rpc.instance_submittable(
        {"StandardGroup": {"GroupMode": "STABLE_CONTROL", "GroupName": "稳定变更"}}
    )[0],
    True,
)
expect(
    "自由操作但已开启安全托管可提交",
    aliyun_rpc.instance_submittable(
        {"StandardGroup": {"GroupMode": "NONE_CONTROL", "GroupName": "自由操作"}, "SellSitd": True}
    )[0],
    True,
)
expect(
    "无 StandardGroup 且未托管不可提交",
    aliyun_rpc.instance_submittable({})[0],
    False,
)

# --- find_databases_by_name：命中即返回，不做多余遍历 ---
def _stub_instances(rows):
    def fn(tid, credentials):
        return rows
    return fn


def _stub_databases(mapping, counter):
    def fn(instance_id, tid, credentials):
        counter.append(instance_id)
        return mapping.get(instance_id, [])
    return fn


def with_stubs(instances, databases, fn):
    orig_i, orig_d = aliyun_rpc.list_instances, aliyun_rpc.list_databases_by_instance
    aliyun_rpc.list_instances = instances
    aliyun_rpc.list_databases_by_instance = databases
    try:
        return fn()
    finally:
        aliyun_rpc.list_instances, aliyun_rpc.list_databases_by_instance = orig_i, orig_d


OK_INST = {"InstanceId": "i-ok", "Host": "h1", "Port": 3306, "InstanceType": "mysql",
           "StandardGroup": {"GroupMode": "STABLE_CONTROL", "GroupName": "稳定变更"}}
BAD_INST = {"InstanceId": "i-bad", "Host": "h2", "Port": 3306, "InstanceType": "mysql",
            "StandardGroup": {"GroupMode": "NONE_CONTROL", "GroupName": "自由操作"}}
DBS = {
    "i-ok": [{"DatabaseId": 1, "SchemaName": "training"}],
    "i-bad": [{"DatabaseId": 2, "SchemaName": "training"}],
}

visited = []
kept, rejected = with_stubs(
    _stub_instances([OK_INST, BAD_INST]),
    _stub_databases(DBS, visited),
    lambda: aliyun_rpc.find_databases_by_name("training", None, CREDS),
)
expect("命中可提交库时只返回该库", [r["DatabaseId"] for r in kept], [1])
expect("命中时不返回排除项", rejected, [])
expect("命中时不遍历不可提交实例", visited, ["i-ok"])
expect("补全实例侧字段", (kept[0]["Host"], kept[0]["DbType"]), ("h1", "mysql"))

visited = []
kept, rejected = with_stubs(
    _stub_instances([BAD_INST]),
    _stub_databases(DBS, visited),
    lambda: aliyun_rpc.find_databases_by_name("training", None, CREDS),
)
expect("全落空时返回空候选", kept, [])
expect("全落空时给出排除原因", [r["DatabaseId"] for r, _ in rejected], [2])
expect("排除原因含管控模式", "自由操作" in rejected[0][1], True)

kept, rejected = with_stubs(
    _stub_instances([OK_INST]),
    _stub_databases(DBS, []),
    lambda: aliyun_rpc.find_databases_by_name("nonexistent", None, CREDS),
)
expect("库名不存在时两者都空", (kept, rejected), ([], []))
expect("库名匹配忽略大小写与空格", len(with_stubs(
    _stub_instances([OK_INST]),
    _stub_databases(DBS, []),
    lambda: aliyun_rpc.find_databases_by_name("  TRAINING  ", None, CREDS),
)[0]), 1)

print(f"\n失败 {len(FAILED)} 项" + (f"：{FAILED}" if FAILED else ""))
sys.exit(1 if FAILED else 0)

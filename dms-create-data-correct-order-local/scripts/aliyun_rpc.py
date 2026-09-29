#!/usr/bin/env python3
"""阿里云 RPC 签名与 DMS 接口调用（凭据只在进程内传递，不落盘）。"""

import base64
import datetime
import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid

ENDPOINT = "https://dms-enterprise.aliyuncs.com/"
API_VERSION = "2018-11-01"


class Credentials:
    """只持有凭据，禁止被打印或序列化，避免误泄漏。"""

    __slots__ = ("access_key_id", "access_key_secret", "security_token")

    def __init__(self, access_key_id, access_key_secret, security_token=None):
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.security_token = security_token or None

    def __repr__(self):
        return "<Credentials 已隐藏>"


def _percent_encode(value):
    return urllib.parse.quote(str(value), safe="-_.~")


def call(action, parameters, credentials, method="GET", timeout=30):
    """发起签名请求，返回 (http_status, response_dict)。网络异常不抛出。"""
    query = {
        "AccessKeyId": credentials.access_key_id,
        "Action": action,
        "Format": "JSON",
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": str(uuid.uuid4()),
        "SignatureVersion": "1.0",
        "Timestamp": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "Version": API_VERSION,
    }
    query.update(parameters)
    if credentials.security_token:
        query["SecurityToken"] = credentials.security_token

    canonical = "&".join(
        f"{_percent_encode(key)}={_percent_encode(query[key])}" for key in sorted(query)
    )
    string_to_sign = f"{method}&%2F&{_percent_encode(canonical)}"
    digest = hmac.new(
        f"{credentials.access_key_secret}&".encode(), string_to_sign.encode(), hashlib.sha1
    ).digest()
    query["Signature"] = base64.b64encode(digest).decode()

    url = ENDPOINT + "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(
        url, data=b"" if method == "POST" else None, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        try:
            return error.code, json.loads(body)
        except json.JSONDecodeError:
            return error.code, {"ErrorMessage": body}
    except urllib.error.URLError as error:
        return 0, {"ErrorMessage": f"网络请求失败：{error.reason}"}


def describe_error(status, response):
    return (
        f"HTTP {status} "
        f"{response.get('ErrorCode') or response.get('Code') or '（未返回错误码）'} "
        f"{response.get('ErrorMessage') or response.get('Message') or '（未返回错误信息）'} "
        f"RequestId={response.get('RequestId', '')}"
    ).strip()


def _as_rows(container, item_key):
    """DMS 列表字段可能是 dict 包裹、裸 list 或单个 dict，统一成 list。"""
    if isinstance(container, dict):
        rows = container.get(item_key, [])
    elif isinstance(container, list):
        rows = container
    else:
        rows = []
    return [rows] if isinstance(rows, dict) else rows


# OpenAPI 提交工单要求：管控模式为安全协同（非 NONE_CONTROL），或已开启安全托管
_FREE_CONTROL = "NONE_CONTROL"

INSTANCE_SOURCES = ("RDS", "ECS", "PUBLIC", "VPC_IDC")


PAGE_SIZE = 100


def _paginate(action, parameters, container_key, item_key, credentials):
    """翻页拉取列表。返回 (行列表, 错误描述列表)。

    错误不静默丢弃：调用方需据此区分「确实没有数据」与「权限不足/接口报错」。
    """
    items, errors = [], []
    page = 1
    while True:
        query = dict(parameters, PageNumber=page, PageSize=PAGE_SIZE)
        status, response = call(action, query, credentials, "GET")
        if response.get("Success") is not True:
            errors.append(describe_error(status, response))
            break
        rows = _as_rows(response.get(container_key), item_key)
        items.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        page += 1
    return items, errors


def list_instances(tid, credentials):
    """列出全部实例，含管控模式与安全托管状态。按 InstanceId 去重。

    全部 InstanceSource 都失败时抛出 RuntimeError，避免把权限问题伪装成「无实例」。
    """
    seen, items, errors = set(), [], []
    for source in INSTANCE_SOURCES:
        parameters = {"InstanceSource": source}
        if tid is not None:
            parameters["Tid"] = tid
        rows, source_errors = _paginate(
            "ListInstances", parameters, "InstanceList", "Instance", credentials
        )
        errors.extend(f"{source}：{item}" for item in source_errors)
        for row in rows:
            key = row.get("InstanceId")
            if key not in seen:
                seen.add(key)
                items.append(row)
    if not items and errors:
        raise RuntimeError("ListInstances 查询失败（需 dms:ListInstances 权限）：\n  - "
                           + "\n  - ".join(errors))
    if errors:
        print(f"提示：部分实例来源查询失败，结果可能不全：{'; '.join(errors)}")
    return items


def list_databases_by_instance(instance_id, tid, credentials):
    """列出指定实例下的全部数据库。接口失败时抛出 RuntimeError。"""
    parameters = {"InstanceId": str(instance_id)}
    if tid is not None:
        parameters["Tid"] = tid
    items, errors = _paginate(
        "ListDatabases", parameters, "DatabaseList", "Database", credentials
    )
    if not items and errors:
        raise RuntimeError(f"ListDatabases 查询失败（实例 {instance_id}，"
                           f"需 dms:ListDatabases 权限）：{'; '.join(errors)}")
    return items


def instance_submittable(instance):
    """判断实例是否满足 OpenAPI 提交工单的前置条件。

    返回 (是否可提交, 原因描述)。
    """
    group = instance.get("StandardGroup") or {}
    mode = group.get("GroupMode")
    name = group.get("GroupName") or "未知"
    hosted = bool(instance.get("SellSitd")) or bool(instance.get("SellTrust"))

    if mode and mode != _FREE_CONTROL:
        return True, f"安全协同（{name}）"
    if hosted:
        return True, "已开启安全托管"
    return False, f"管控模式为 {name}（{mode}）且未开启安全托管"


def _match_databases(instance, wanted, reason, tid, credentials):
    """在单个实例中找同名库，补全实例侧字段后返回匹配行。"""
    iid = instance.get("InstanceId")
    if iid is None:
        return []
    matched = []
    for db in list_databases_by_instance(iid, tid, credentials):
        if str(db.get("SchemaName") or "").strip().lower() != wanted:
            continue
        row = dict(db)
        row.setdefault("Host", instance.get("Host"))
        row.setdefault("Port", instance.get("Port"))
        row.setdefault("DbType", instance.get("InstanceType"))
        row["InstanceId"] = iid
        row["InstanceAlias"] = instance.get("InstanceAlias") or "未命名实例"
        row["ControlModeReason"] = reason
        matched.append(row)
    return matched


def find_databases_by_name(schema_name, tid, credentials):
    """遍历实例查找同名库，返回 (可提交候选, [(库行, 排除原因), ...])。

    SearchDatabase 对同名库只返回部分结果，可能漏掉安全协同实例上的库，
    因此直接遍历实例的数据库列表做精确匹配。

    先只扫可提交实例；命中即返回，不为诊断多付一遍遍历的代价。
    全部落空时才扫不可提交实例，用于向用户说明库在哪、为何不能提交。
    """
    wanted = str(schema_name).strip().lower()
    instances = list_instances(tid, credentials)
    verdicts = [(inst, *instance_submittable(inst)) for inst in instances]

    kept = []
    for inst, submittable, reason in verdicts:
        if submittable:
            kept.extend(_match_databases(inst, wanted, reason, tid, credentials))
    if kept:
        return kept, []

    rejected = []
    for inst, submittable, reason in verdicts:
        if not submittable:
            rejected.extend(
                (row, reason) for row in _match_databases(inst, wanted, reason, tid, credentials)
            )
    return [], rejected


def search_database(database_name, tid, credentials):
    """按名称模糊查询数据库，返回候选列表。"""
    parameters = {
        "SearchKey": database_name,
        "PageNumber": 1,
        "PageSize": 50,
        "SearchTarget": "DB",
    }
    if tid is not None:
        parameters["Tid"] = tid
    status, response = call("SearchDatabase", parameters, credentials, "GET")
    if response.get("Success") is not True:
        raise RuntimeError(f"数据库查询失败：{describe_error(status, response)}")
    return _as_rows(response.get("SearchDatabaseList"), "SearchDatabase")


def create_data_correct_order(order, credentials):
    """提交数据变更工单。order 为 order_spec.build_order 的产物。"""
    parameters = {
        "Comment": order["Comment"],
        "Param": json.dumps(order["Param"], ensure_ascii=False, separators=(",", ":")),
    }
    for key in ("Tid", "RealLoginUserUid"):
        if key in order:
            parameters[key] = order[key]
    return call("CreateDataCorrectOrder", parameters, credentials, "POST")


def get_order_base_info(order_id, tid, credentials):
    """回查工单状态，用于确认工单是否真正进入审批流。"""
    parameters = {"OrderId": order_id}
    if tid is not None:
        parameters["Tid"] = tid
    return call("GetOrderBaseInfo", parameters, credentials, "GET")


def get_data_correct_order_detail(order_id, tid, credentials):
    """查询数据变更预检查状态；创建成功不代表异步预检查已完成。"""
    parameters = {"OrderId": order_id}
    if tid is not None:
        parameters["Tid"] = tid
    return call("GetDataCorrectOrderDetail", parameters, credentials, "GET")


def submit_order_approval(order_id, tid, credentials):
    """提交工单审批，将状态从 new 推进到 toaudit。"""
    parameters = {"OrderId": order_id}
    if tid is not None:
        parameters["Tid"] = tid
    return call("SubmitOrderApproval", parameters, credentials, "POST")

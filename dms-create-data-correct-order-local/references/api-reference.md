# CreateDataCorrectOrder 参数参考

官方文档：<https://www.alibabacloud.com/help/zh/dms/developer-reference/api-dms-enterprise-2018-11-01-createdatacorrectorder>

API 版本 `2018-11-01`，RPC 风格，HMAC-SHA1 签名。目标实例须为安全协同模式或已开启安全托管。

## 调用链

| 顺序 | 接口 | 作用 | RAM Action |
| --- | --- | --- | --- |
| 1 | `ListInstances` / `ListDatabases` | 遍历可提交实例，解析真实目标库 | `dms:ListInstances` / `dms:ListDatabases` |
| 2 | `CreateDataCorrectOrder` | 创建工单，预检查异步进行 | `dms:CreateDataCorrectOrder` |
| 3 | `GetDataCorrectOrderDetail` | 等待 Status=precheck_success | `dms:GetDataCorrectOrderDetail` |
| 4 | `SubmitOrderApproval` | 提交现有工单审批 | `dms:SubmitOrderApproval` |
| 5 | `GetOrderBaseInfo` | 回查是否真正进入审批流程 | `dms:GetOrderBaseInfo` |

`SearchDatabase` 仅用于显式开启快速查询的模式；同名库通过 `database_filter` 或人工选择精确定位。选中项的 `DatabaseId` 与 `Logic` 写入 `Param.DbItemList`，返回的 `DbType` 用于判定 SQL 方言。

## 必填参数

| 参数 | 说明 |
| --- | --- |
| `Comment` | 工单业务背景，审批依据 |
| `Param.EstimateAffectRows` | 预估影响行数 |
| `Param.SqlType` | `TEXT` 或 `ATTACHMENT` |
| `Param.ExecSQL` | `SqlType=TEXT` 时必填 |
| `Param.AttachmentName` | `SqlType=ATTACHMENT` 时必填，值为上传任务返回的 Key，不是本地路径 |
| `Param.DbItemList` | 变更目标列表，每项用 `DbId` 或 `InstanceId`，不可同时给 |

## 条件与可选参数

| 参数 | 说明 |
| --- | --- |
| `Param.ExecMode` | `COMMITOR`（默认）、`AUTO`、`LAST_AUDITOR` |
| `Param.Classify` | 数据变更原因 |
| `Param.RollbackSqlType` | `TEXT` 或 `ATTACHMENT`，本 Skill 强制要求 |
| `Param.RollbackSQL` | `RollbackSqlType=TEXT` 时必填 |
| `Param.RollbackAttachmentName` | `RollbackSqlType=ATTACHMENT` 时必填 |
| `Param.AttachmentKey` | 补充工单信息的附件 Key，与 SQL 附件无关 |
| `Tid` | DMS 租户 ID，可由 `GetUserActiveTenant` 或 `ListUserTenants` 获取 |
| `RealLoginUserUid` | 实际调用 API 的阿里云账号 UID |
| `RelatedUserList` | 相关用户 UID 数组，这些人可查看并协同该工单 |

`Param` 需序列化为 JSON 字符串后作为单个请求参数传递。使用 `DbId` 时 `Logic=true` 表示逻辑库。

`DbItemList` 每项同时传 `DbId` 和 `InstanceId` 时，DMS 按 `DbId` 处理并忽略 `InstanceId`；本 Skill 直接拒绝这种写法，避免歧义。实例级变更（`InstanceId`）目前仅支持 RDS MySQL、PolarDB MySQL 版和 AnalyticDB for MySQL。

本 Skill 未使用 `RelatedUserList` 和 `Param.AttachmentKey`，如需协同人或补充附件，须手动扩展 `order_spec.build_order`。

## 本 Skill 附加的约束

超出 API 要求、为降低误更新风险而加的校验：

- UPDATE/DELETE 必须带 WHERE。
- MySQL 系方言的 UPDATE/DELETE 必须带 LIMIT 正整数；PostgreSQL、SQLServer 等不支持该子句的方言不强制。
- 判定 WHERE/LIMIT 前先剥离字符串字面量与注释，避免 `SET note='LIMIT 100'` 或 `-- LIMIT 5` 骗过校验。
- 拆分语句时忽略字面量与注释内的分号。
- 回滚语句数必须等于执行语句数。INSERT 作为 DELETE 的回滚时不要求 LIMIT。
- `Comment` 至少 15 字且不得为空泛描述。
- 文本 SQL UTF-8 上限 1024 字节，超出须改用附件。
- 附件内容脚本无法读取，须显式确认已人工核对。

## 响应判断

只有 `Success=true` 表示创建成功，`CreateOrderResult` 含工单号。不要以 HTTP 200 单独判断。错误同时兼容 `ErrorCode/ErrorMessage` 和网关的 `Code/Message`，保留 HTTP 状态和 RequestId。创建超时或响应未知时不得自动重新创建，先核实原工单；送审失败则回查原工单状态。

## 审批状态

创建成功不代表预检查完成或已进入审批。`GetDataCorrectOrderDetail.DataCorrectOrderDetail.Status` 的 `new/precheck` 表示仍在准备，`precheck_fail` 表示预检查失败，只有 `precheck_success` 才可送审。失败步骤在 `PreCheckDetail.TaskCheckDO` 中，读取 `CheckStep`、`CheckStatus` 和 `UserTip`。

`GetOrderBaseInfo.OrderBaseInfo.StatusCode=new` 表示待提交审批，`toaudit/auditing` 才表示审批中。审批通过及执行阶段按实际状态报告，不当成审批中。脚本不调用执行工单接口。

- [预检查详情接口](https://help.aliyun.com/zh/dms/developer-reference/api-dms-enterprise-2018-11-01-getdatacorrectorderdetail)
- [提交审批接口](https://help.aliyun.com/zh/dms/developer-reference/api-dms-enterprise-2018-11-01-submitorderapproval)
- [审批流配置](https://help.aliyun.com/zh/dms/configure-approval-processes)

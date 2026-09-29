---
name: dms-create-data-correct-order-local
description: 需要在阿里云 DMS 提交数据变更工单并送审时使用。适用于用户描述了要改哪张表的哪些数据、要求发起数据变更审批、只知道数据库名称不知道 DbId 或 InstanceId、或需要为 UPDATE/DELETE 准备回滚语句的场景。
---

# DMS 数据变更工单

根据已核实的数据生成执行 SQL、回滚 SQL 和工单规格，创建工单，等待预检查通过后提交审批，最后回查真实状态。

## 凭据

本机凭据已按用户授权保存在技能根目录的 `credentials.local.json`，文件权限仅授予当前 Windows 用户，并在 `.gitignore` 中排除。脚本自动读取，无需用户每次提供。

读取顺序：

1. 完整的环境变量 `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET`，可选 `ALIBABA_CLOUD_SECURITY_TOKEN`。
2. 环境变量未设置 AccessKey 时，读取 `credentials.local.json` 的 `access_key_id`、`access_key_secret` 和可选 `security_token`。
3. 前两者均未配置时才交互询问，Secret 使用隐藏输入。部分环境变量不会与本机文件混搭。

不要读取或回显凭据文件内容，不将密钥写入 SKILL.md、工单规格、SQL、日志或测试。分享、复制或打包技能时必须排除 `credentials.local.json`；Git 忽略规则不等于打包排除规则。仅检查环境变量为空不能判断“没有凭据”，应通过 `prompts.read_credentials()` 读取。

## 执行边界

- 提交前展示不含凭据的完整请求预览。会话中已明确授权相同目标、SQL 和影响范围时继续执行，不重复索要确认；范围改变或尚未授权时才确认。
- 目标表、筛选条件、原值和业务原因优先沿用会话事实，仅询问缺失且无法核实的信息。
- 不猜测 DbId、InstanceId、Tid、SQL 或影响行数。实例须为安全协同模式或已开启安全托管，默认自动筛选。
- 默认 `ExecMode=COMMITOR`，审批后由提交人手动执行。只有用户明确要求时才用 `AUTO`。脚本不会调用数据库执行工单的接口。
- 创建请求失败或结果不明时，不自动重新创建；先回查是否已生成。已有工单只按原工单号继续。

## 工作流程

1. 只读查询真实原值和匹配行数，生成执行 SQL 及逐条对应的回滚 SQL。DELETE 的回滚必须恢复实际原值。
2. 使用 `references/order-spec-template.json` 生成规格文件，删除 `_` 开头的说明字段。`classify` 必填，通常为“数据订正”。
3. 离线校验，不联网、不读取凭据：

   ```bash
   python scripts/create_order.py --spec order.json --dry-run
   ```

4. 创建并送审：

   ```bash
   python scripts/create_order.py --spec order.json
   ```

   脚本自动解析目标库，展示完整预览，等待输入大写 `YES`。目标需与已核实的业务库一致；存在多个候选时通过 `database_filter` 精确指定。

5. 创建成功后保存工单号。脚本先轮询 `GetDataCorrectOrderDetail`，只有 `Status=precheck_success` 才调用 `SubmitOrderApproval`，然后调用 `GetOrderBaseInfo` 回查。
6. 报告工单号、实际审批状态和执行方式。创建成功不能替代“已进入审批”的确认。

本机直连 DMS 目标地址超时时，可通过 `ExecuteScript` 对已解析 DbId 执行只读 SELECT 核对数据；不要据此切换到其他同名库。只读副本可能拒绝 `EXPLAIN DELETE/UPDATE`，核查影响范围时使用相同 WHERE 条件的 SELECT。

## 预检查与送审恢复

- 预检查是异步的，创建成功后不能立即送审。默认每 5 秒查询一次，等待上限 120 秒；失败、查询错误、未知状态或超时均停止送审并保留工单号。
- `new` 表示待提交审批；只有 `toaudit/auditing` 表示审批中。其他状态按接口实际返回描述报告。
- 送审失败时回查原工单，若已在审批中则确认成功；仍为 `new` 或回查失败则返回非零退出码。不要自动重复创建或连续重试送审。
- 修复已确认的错误或等待预检查完成后，可继续原工单：

  ```bash
  python scripts/create_order.py --order-id 27903311
  ```

  工单号仅为用法示例，必须替换为本次实际工单号。此命令展示现有工单详情并确认，只回查及继续审批，不创建新工单，也不执行 SQL。
- 错误输出同时兼容 `ErrorCode/ErrorMessage` 和 `Code/Message`，保留 HTTP 状态及 RequestId。HTTP 403 本身不足以判断是权限问题，需看具体错误及工单状态。
- 退出码：`0` 表示流程完成、校验通过或用户取消，`1` 为准备失败，`2` 为创建失败，`3` 为工单已存在但未确认送审成功。

## 已授权的自动化

准确规格或原工单已被用户授权时，可同时使用 `--yes` 和 `DMS_ALLOW_UNATTENDED=1` 跳过终端重复确认；仍会展示预览。只给其中一项不会跳过。

Windows PowerShell：

```powershell
$env:DMS_ALLOW_UNATTENDED = '1'
python scripts/create_order.py --spec order.json --yes
# 恢复已授权的原工单：
python scripts/create_order.py --order-id 27903311 --yes
```

## SQL 与规格校验

| 检查项 | 规则 |
| --- | --- |
| WHERE / LIMIT | 所有 UPDATE/DELETE 必须有 WHERE；MySQL 系还须有 LIMIT 正整数 |
| 字面量和注释 | 其中的 LIMIT 不计入安全校验 |
| 回滚条数 | 与执行语句逐条对应、数量相同；DELETE 的回滚 INSERT 无需 LIMIT |
| Comment | 至少 15 字，具体写明表、筛选条件、变更内容和业务原因 |
| Classify | 必填；缺失会被服务端拒绝 |
| TEXT SQL | 执行和回滚文本分别不得超过 1024 字节 |
| 附件 | 须显式设置 attachment_sql_verified=true 并提供语句条数 |
| ExecMode | 仅 COMMITOR、AUTO、LAST_AUDITOR |

## 数据库解析与权限

默认通过 `ListInstances` / `ListDatabases` 遍历可提交实例，以免 `SearchDatabase` 漏掉同名库。仅在没有可提交候选时补查不可提交实例并报告原因。多个候选不得猜选，应使用 `database_filter`，例如精确的 Host 和 EnvType。

`skip_control_mode_check=true` 会退回 SearchDatabase，仅在已核实目标实例可提交时使用。提供明确 `target` 也会跳过实例解析，须事先核实归属和管控模式。

所需权限：`dms:ListInstances`、`dms:ListDatabases`、`dms:CreateDataCorrectOrder`、`dms:GetDataCorrectOrderDetail`、`dms:GetOrderBaseInfo`、`dms:SubmitOrderApproval`。快速查询模式另需 `dms:SearchDatabase`；通过 DMS 只读核对数据时需 `dms:ExecuteScript`。

## 资源与验证

- `scripts/create_order.py`：创建、预检查等待、送审和按工单号恢复。
- `scripts/prompts.py`：环境变量、本机凭据及交互输入。
- `scripts/aliyun_rpc.py`：签名、查询、提交及错误信息。
- `scripts/order_spec.py`、`scripts/sql_guard.py`：规格与 SQL 校验。
- [规格模板](references/order-spec-template.json)：编写本次工单。
- [API 参数说明](references/api-reference.md)：核对接口字段与状态语义。

修改脚本后运行离线回归；测试使用模拟凭据和接口，禁止为测试创建真实工单：

```bash
python scripts/test_sql_guard.py
python scripts/test_aliyun_rpc.py
python scripts/test_workflow.py
```

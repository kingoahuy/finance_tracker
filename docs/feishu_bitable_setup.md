# 飞书多维表格配置

## 推荐字段

请在目标多维表格中按以下名称创建字段。字段名称需要与代码完全一致：

| 字段 | 推荐类型 | 用途 |
| --- | --- | --- |
| 交易UID | 文本 | SQLite 与飞书之间的幂等主键 |
| 本地ID | 数字 | SQLite 流水 ID |
| 日期 | 日期 | 交易日期 |
| 类型 | 单选 | 收入、支出 |
| 分类 | 单选 | 餐饮、交通、工资等 |
| 金额 | 数字 | 正数金额 |
| 净额 | 公式 | `IF(类型="收入", 金额, -金额)` |
| 描述 | 文本 | 交易说明 |
| 标签 | 多选 | 专项或自定义标签 |
| 是否刚需 | 复选框 | 刚需标记 |
| 是否固定 | 复选框 | 固定收支标记 |
| 录入来源 | 单选 | streamlit、feishu、cli |
| 飞书消息ID | 文本 | 消息防重复审计 |
| 创建时间 | 日期时间 | 本地创建时间 |
| 更新时间 | 日期时间 | 本地更新时间 |
| 状态 | 单选或文本 | `active`、`deleted` |
| 删除时间 | 日期时间 | 软删除时间 |
| 删除人 | 文本 | 删除操作审计标识哈希，不写入完整 open_id |
| 删除原因 | 文本 | 软删除原因 |
| 年月 | 公式 | 日期格式化为 `YYYY-MM` |
| 年份 | 公式 | 日期年份 |

代码写入的集中字段映射位于 `finance_tracker/bitable_sync.py` 的 `FIELD_MAP`。

## 获取 app_token 和 table_id

1. 打开多维表格。
2. 从 URL 或飞书开发者工具中获取 `app_token`。
3. 打开目标数据表，从 URL/接口信息获取 `table_id`。
4. 将自建应用添加为多维表格协作者，并授予可编辑权限。
5. 填入本地 `.env`：

```dotenv
FEISHU_BITABLE_APP_TOKEN=xxxxxxxxx
FEISHU_BITABLE_TABLE_ID=tblxxxxxxxxx
FEISHU_BITABLE_SYNC_ENABLED=true
FEISHU_AUTO_SYNC=true
FEISHU_BITABLE_TIMEOUT_SECONDS=15
```

不要把完整多维表格 URL 填入变量。`APP_TOKEN` 与 `TABLE_ID` 是两个独立值。

## 应用权限与协作者

1. 在飞书开放平台为自建应用开通多维表格读取、记录新增、记录更新权限。推荐开通 `bitable:app`；若使用细分权限，字段读取至少需要 `base:field:read` 或 `bitable:app:readonly`，并同时开通记录读取、`base:record:create` 和记录更新权限。
2. 权限变更后发布新的应用版本。
3. 在目标多维表格的协作者设置中添加该自建应用，并授予可编辑权限。
4. 若返回 `Access denied` 或提示 `base:record:create`，说明应用权限或表格协作者权限仍未生效。

## 检查与同步

```powershell
# 检查环境配置、API 连接和字段
.\.venv\Scripts\python.exe -m finance_tracker.bitable_sync --check

# 重试待同步流水
.\.venv\Scripts\python.exe -m finance_tracker.bitable_sync --pending

# 仅同步第一条待处理或失败流水，适合排查
.\.venv\Scripts\python.exe -m finance_tracker.bitable_sync --one

# 将 SQLite 中所有流水幂等同步到飞书
.\.venv\Scripts\python.exe -m finance_tracker.bitable_sync --full

# 将历史失败状态安全恢复为待同步
.\.venv\Scripts\python.exe -m finance_tracker.bitable_sync --reset-failed

# 审计远端记录、重复 UID、孤儿和测试记录
.\.venv\Scripts\python.exe -m finance_tracker.bitable_sync --audit-remote

# 预览重复记录删除计划，不会真正删除
.\.venv\Scripts\python.exe -m finance_tracker.bitable_sync --dedupe-remote --dry-run

# 确认 dry-run 后，才执行远端去重
.\.venv\Scripts\python.exe -m finance_tracker.bitable_sync --dedupe-remote --apply

# 只预览或清理明显测试记录
.\.venv\Scripts\python.exe -m finance_tracker.bitable_sync --cleanup-test-records --dry-run
.\.venv\Scripts\python.exe -m finance_tracker.bitable_sync --cleanup-test-records --apply
```

`--check` 会列出缺少的字段，并在 API 失败时返回 `code`、`message` 和 `log_id`，但不会输出 App Secret、完整用户 ID 或账本内容。

`--pending` 和 `--full` 每处理 10 条打印一次进度，单条失败会立即打印。`--one` 只输出本地 ID、交易 UID 前 8 位和飞书 API 结果，适合先确认单条同步是否正常。

`--reset-failed` 只把本地失败流水和未完成的失败 outbox 任务恢复为待同步，并清空旧错误与重试次数；不会删除 SQLite 流水，也不会删除飞书多维表格记录。

`--dedupe-remote` 以“交易UID”为唯一键，优先保留本地 `feishu_record_id` 已绑定的记录。`--cleanup-test-records` 只匹配空 UID 且空本地 ID、`test_` UID 或描述包含“权限测试”的明显测试记录。两个清理命令都必须明确选择 `--dry-run` 或 `--apply`。

## 仪表盘建议

在多维表格中基于同一数据表创建：

- 本月收入：筛选本月、类型为收入，汇总金额。
- 本月支出：筛选本月、类型为支出，汇总金额。
- 本月结余：汇总净额。
- 预算使用率：本月支出除以预算基准。
- 每日支出趋势：日期分组，筛选支出，折线图。
- 月度收支趋势：年月分组，按类型拆分。
- 支出分类占比：筛选支出，按分类饼图。
- 刚需与非刚需占比：按“是否刚需”分组。
- 固定与非固定支出占比：按“是否固定”分组。
- 最近流水：按日期、创建时间倒序的表格视图。

预算使用率需要在飞书侧设置预算常量或关联预算表；账本的真实预算统计仍以 SQLite/现有报告逻辑为准。

## 同步规则

- SQLite 是唯一主数据源，多维表格是分析镜像。
- `交易UID` 用于搜索已有记录，`feishu_record_id` 用于快速更新。
- 全量同步会先查找 UID，因此可重复执行，不应重复创建。
- 同步失败会进入 `sync_outbox`，不会影响本地记账成功。
- 第一阶段不支持从多维表格反向修改 SQLite。

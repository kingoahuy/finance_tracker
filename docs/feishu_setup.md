# 飞书机器人接入指南

## 1. 创建自建应用

1. 打开飞书开放平台，创建企业自建应用。
2. 在“凭证与基础信息”中获取 App ID 和 App Secret。
3. 开启“机器人”能力，机器人名称可设置为“智账 Pro”。
4. 不要把 App Secret 截图、写入 README 或提交到 Git。

## 2. 配置权限

按飞书控制台当前名称申请并发布以下能力：

- 读取用户发给机器人的消息事件。
- 以应用身份发送消息。
- 读取和写入多维表格记录。
- 如在群聊使用，允许机器人加入目标群聊。

权限名称可能随飞书后台版本调整，以 `im.message.receive_v1` 和多维表格记录读写 API 所需权限为准。

## 3. 配置长连接事件

1. 在“事件与回调”中选择长连接模式。
2. 添加事件 `im.message.receive_v1`，并启用卡片回传交互 `card.action.trigger`。
3. 无需公网 IP、域名或 HTTP 回调地址。
4. 发布应用版本，并确保应用在当前企业内可用。

## 4. 首次获取 open_id

先在本机 `.env` 设置：

```dotenv
FEISHU_BOOTSTRAP_MODE=true
FEISHU_ALLOWED_OPEN_IDS=
```

完整 `open_id` 不会写入日志。请在飞书开放平台的事件调试信息中获取自己的 `open_id`，填入本机 `.env` 后立即关闭 Bootstrap 模式。

获取后立即关闭 Bootstrap 模式：

```dotenv
FEISHU_BOOTSTRAP_MODE=false
FEISHU_ALLOWED_OPEN_IDS=ou_xxxxxxxxx
FEISHU_ALLOWED_CHAT_IDS=oc_xxxxxxxxx
```

Bootstrap 模式不会记账。`open_id` 和 `chat_id` 也属于个人/组织标识，不应提交到 Git。

## 5. 配置环境变量

复制 `.env.example` 为 `.env`，填写：

```dotenv
FEISHU_APP_ID=cli_xxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxx
FEISHU_VERIFICATION_TOKEN=xxxxxxxxx
FEISHU_ENCRYPT_KEY=xxxxxxxxx
FEISHU_ALLOWED_OPEN_IDS=ou_xxxxxxxxx
FEISHU_ALLOWED_CHAT_IDS=
FEISHU_BOOTSTRAP_MODE=false
FEISHU_BITABLE_APP_TOKEN=xxxxxxxxx
FEISHU_BITABLE_TABLE_ID=tblxxxxxxxxx
FEISHU_BOT_ENABLED=true
FEISHU_BITABLE_SYNC_ENABLED=true
FEISHU_AUTO_SYNC=true
FEISHU_LOG_LEVEL=INFO
FEISHU_SYNC_RETRY_LIMIT=5
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
AI_PARSER_ENABLED=true
AI_PARSER_REQUIRE_CONFIRMATION=true
AI_PARSER_TIMEOUT_SECONDS=15
AI_PARSER_FALLBACK_TO_LOCAL=true
```

多个白名单 ID 使用英文逗号分隔。群聊中机器人仅在被 @ 时响应。

## 6. 安装和启动

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\backup_database.ps1
.\start_all.bat
.\service_status.bat
```

服务管理器会同时守护：

- Streamlit
- 邮件 scheduler
- 飞书机器人（启用且 App ID/Secret 完整时）

修改飞书配置后，执行：

```powershell
.\stop_services.bat
.\start_all.bat
```

## 7. 可用命令

- `帮助`
- `今日账单`
- `本月账单`
- `最近5笔`
- `最近N笔`
- `生成日报`
- `同步看板`
- `撤销上一笔`
- `删除 ID 12`
- 普通记账文本，例如 `昨天打车36.5，晚饭42`

记账、删除和修改默认需要在飞书卡片中二次确认，卡片 10 分钟后过期。重复确认不会重复写入。删除是软删除，仅限操作者本人在当前会话通过飞书创建的有效流水。

## 8. 多维表格同步

```powershell
# 重试待同步任务
.\.venv\Scripts\python.exe -m finance_tracker.bitable_sync --pending

# 幂等全量同步
.\.venv\Scripts\python.exe -m finance_tracker.bitable_sync --full
```

SQLite 是主数据源。飞书同步失败不会回滚本地账单，scheduler 会继续重试。

## 9. 排查机器人离线

1. 运行 `.\service_status.bat`，确认 `FeishuBotProcessId` 存在。
2. 检查 `logs/feishu_bot.err.log` 和 `logs/feishu_bot.log`。
3. 检查 App ID、App Secret、应用版本发布状态。
4. 检查事件订阅是否为长连接，事件是否包含 `im.message.receive_v1`。
5. 检查发送者和会话是否在白名单内。
6. 修改 `.env` 后重启全部服务。

日志不会输出 App Secret，也不会记录完整账单消息正文。

## 10. AI 解析与隐私

- 仅当 `AI_PARSER_ENABLED=true` 且配置了密钥时，才会调用兼容 OpenAI 的 DeepSeek 接口。

## DeepSeek 智能对话边界

DeepSeek 只负责把用户消息解析为结构化 JSON，包括意图、交易草稿、追问、
修订内容和查询参数。它不会获得 SQLite 连接，也不能直接新增、修改、删除流水
或触发多维表格写入。

本地 Python 负责：

- 校验金额、日期、类型与分类；
- 金额缺失时保存 10 分钟短期会话并继续追问；
- 为新增、修改、删除生成 `pending_actions`；
- 处理卡片确认以及“确认、可以、记上、取消、算了”等文本操作；
- 执行 SQLite 写入并沿用原有多维表格同步；
- AI 超时、JSON 错误或低置信度时回退本地规则。

短期上下文保存在 `feishu_sessions`。身份只保存 SHA-256 哈希，会话只保留最近
必要的交易草稿和意图摘要，默认 10 分钟过期。
- AI 只返回结构化意图，不直接写数据库。
- 低置信度、超时、接口错误或无密钥时自动降级到本地解析。
- 审计日志只记录消息哈希、长度、意图和置信度，不记录消息正文、密钥或完整 `open_id`。
- `FEISHU_DAILY_REPORT_*` 当前仅预留配置，不会自动推送。

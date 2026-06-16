# 智账 Pro

本地个人账本：Streamlit 可视化 + SQLite 存储 + 邮件日报 + 飞书机器人移动记账。

## 功能概览

- **Streamlit 记账面板** — 收支录入、分类筛选、趋势图表
- **命令行工具** — 文本 / JSON 快速记账，生成日报
- **邮件日报** — 定时生成并发送每日财务摘要
- **飞书机器人** — 长连接自建应用，群聊或私聊发消息即可记账
- **AI 语义解析** — 可选接入 DeepSeek，自然语言自动解析记账意图；未配置时回退到本地解析器
- **确认卡片** — 记账、删除、修改操作均先返回飞书确认卡片，确认后才写入数据库
- **多维表格同步** — 流水单向同步到飞书多维表格（Bitable），支持自动 / 手动同步
- **定时任务** — 后台调度器自动日报、自动同步

## 快速开始

```powershell
# 1. 克隆仓库
git clone https://github.com/kingoahuy/finance_tracker.git
cd finance_tracker

# 2. 创建虚拟环境并安装依赖
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# 3. 配置环境变量
copy .env.example .env
# 编辑 .env 填入你的邮箱配置（邮件功能可选）

# 4. 初始化数据库
python init_db.py

# 5. 启动
python -m streamlit run finance_tracker\app.py
```

浏览器访问 `http://127.0.0.1:8501`。

## 环境变量

复制 `.env.example` 为 `.env`，按需填写：

### 基础配置

| 变量 | 说明 | 必填 |
| --- | --- | --- |
| `FINANCE_DB_FILE` | 数据库文件路径，默认 `my_account_book.db` | 否 |
| `FINANCE_MONTHLY_BUDGET` | 月度预算（元），默认 2000 | 否 |

### 邮件日报

| 变量 | 说明 | 必填 |
| --- | --- | --- |
| `FINANCE_MAIL_HOST` | SMTP 服务器，默认 `smtp.qq.com` | 邮件功能需要 |
| `FINANCE_MAIL_USER` | 发件邮箱 | 邮件功能需要 |
| `FINANCE_MAIL_PASS` | 邮箱授权码（非登录密码） | 邮件功能需要 |
| `FINANCE_MAIL_RECEIVERS` | 收件人，多个用逗号分隔 | 邮件功能需要 |

### 飞书机器人

| 变量 | 说明 | 必填 |
| --- | --- | --- |
| `FEISHU_APP_ID` | 飞书自建应用 App ID | 飞书功能需要 |
| `FEISHU_APP_SECRET` | 飞书自建应用 App Secret | 飞书功能需要 |
| `FEISHU_VERIFICATION_TOKEN` | 事件订阅验证 Token | 飞书功能需要 |
| `FEISHU_ENCRYPT_KEY` | 事件加密密钥（可留空） | 否 |
| `FEISHU_ALLOWED_OPEN_IDS` | 允许使用的用户 Open ID，多个用逗号分隔 | 否 |
| `FEISHU_ALLOWED_CHAT_IDS` | 允许使用的群聊 ID，多个用逗号分隔 | 否 |
| `FEISHU_BOT_ENABLED` | 是否启用飞书机器人，默认 `true` | 否 |
| `FEISHU_BOOTSTRAP_MODE` | 启动时是否自动同步历史数据，默认 `false` | 否 |
| `FEISHU_LOG_LEVEL` | 日志级别，默认 `INFO` | 否 |

### 多维表格同步

| 变量 | 说明 | 必填 |
| --- | --- | --- |
| `FEISHU_BITABLE_APP_TOKEN` | 多维表格 App Token | 多维表格同步需要 |
| `FEISHU_BITABLE_TABLE_ID` | 多维表格数据表 ID | 多维表格同步需要 |
| `FEISHU_BITABLE_SYNC_ENABLED` | 是否启用多维表格同步，默认 `true` | 否 |
| `FEISHU_AUTO_SYNC` | 记账后自动同步，默认 `true` | 否 |
| `FEISHU_BITABLE_TIMEOUT_SECONDS` | 同步超时（秒），默认 15 | 否 |
| `FEISHU_SYNC_RETRY_LIMIT` | 同步失败重试次数，默认 5 | 否 |
| `FEISHU_DAILY_REPORT_ENABLED` | 飞书日报开关，默认 `false` | 否 |
| `FEISHU_DAILY_REPORT_TIME` | 飞书日报发送时间，默认 `21:30` | 否 |

### AI 语义解析（可选）

| 变量 | 说明 | 必填 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | AI 解析需要 |
| `DEEPSEEK_BASE_URL` | API 地址，默认 `https://api.deepseek.com` | 否 |
| `DEEPSEEK_MODEL` | 模型名，默认 `deepseek-v4-flash` | 否 |
| `AI_PARSER_ENABLED` | 是否启用 AI 解析，默认 `true` | 否 |
| `AI_PARSER_REQUIRE_CONFIRMATION` | AI 解析结果是否需要用户确认，默认 `true` | 否 |
| `AI_PARSER_TIMEOUT_SECONDS` | AI 解析超时（秒），默认 15 | 否 |
| `AI_PARSER_FALLBACK_TO_LOCAL` | AI 失败时是否回退到本地解析器，默认 `true` | 否 |

## 隐私保护

- `.env`、SQLite 数据库、运行日志、导出文件和本地备份默认不会提交到 Git。
- 仓库只提供 `.env.example`，请勿把真实邮箱、授权码或个人账单写入示例文件。
- 部署或分享前，可运行 `git status --ignored` 检查本地隐私文件是否处于忽略状态。

## 命令行记账

```powershell
# 文本记账
.venv\Scripts\python.exe finance_tracker\account_ops.py add-text "午饭 25; 地铁 4"

# JSON 记账
$json = '[{"date":"2026-06-05","type":"支出","category":"餐饮","amount":25,"description":"午饭"}]'
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
.venv\Scripts\python.exe finance_tracker\account_ops.py add-json --base64 $b64

# 查看最近记录
.venv\Scripts\python.exe finance_tracker\account_ops.py recent --limit 10
```

## 邮件日报

```powershell
# 生成报告
.venv\Scripts\python.exe finance_tracker\account_ops.py report --date 2026-06-05

# 发送邮件
.venv\Scripts\python.exe finance_tracker\account_ops.py send-report --date 2026-06-05

# 定时发送
.venv\Scripts\python.exe finance_tracker\account_ops.py schedule-report --report-date 2026-06-05 --send-at "2026-06-06 08:00"
```

## 一键启动（Windows）

```powershell
.\start_all.bat        # 启动 Streamlit + 调度器
.\service_status.bat   # 查看状态
.\stop_services.bat    # 停止服务
```

开机自启：

```powershell
.\install_startup.bat    # 安装
.\uninstall_startup.bat  # 卸载
```

## 飞书移动记账

项目支持飞书自建应用长连接机器人，与 Streamlit 共用同一个 SQLite 账本，并可将流水单向同步到飞书多维表格。

**使用方式：** 在飞书群聊或私聊中发送自然语言即可记账，例如"午饭 25 元"、"删除昨天的地铁记录"。

**安全机制：**

- 普通记账、删除和修改操作会先返回飞书确认卡片，确认后才写入 SQLite
- AI 只负责把自然语言解析为结构化动作；金额、日期、分类、用户归属和最终写入都由本地 Python 校验
- 未配置 AI 或请求失败时自动回退到本地正则解析器
- 支持用户白名单（`FEISHU_ALLOWED_OPEN_IDS`）和群聊白名单（`FEISHU_ALLOWED_CHAT_IDS`）

**快速启动：**

```powershell
.\scripts\backup_database.ps1          # 备份数据库
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start_all.bat                        # 启动 Streamlit + 调度器
```

配置步骤见：

- [飞书机器人接入指南](docs/feishu_setup.md)
- [飞书多维表格配置](docs/feishu_bitable_setup.md)

## 项目结构

```
finance_tracker/
  app.py                 # Streamlit 界面
  ledger.py              # 核心记账逻辑与数据库
  config.py              # 环境变量加载
  email_service.py       # 邮件日报生成与发送
  scheduler.py           # 后台定时任务调度
  account_ops.py         # 命令行工具
  service_runner.py      # 进程管理
  analytics.py           # 数据分析与统计
  tagging.py             # 分类标签管理
  ai_parser.py           # AI 语义解析（DeepSeek）
  transaction_service.py # 事务处理服务
  feishu_bot.py          # 飞书长连接机器人
  feishu_client.py       # 飞书 API 封装
  feishu_config.py       # 飞书配置加载
  feishu_commands.py     # 飞书命令处理
  feishu_menu_dispatcher.py  # 飞书菜单事件分发
  feishu_report.py       # 飞书日报生成
  bitable_sync.py        # 多维表格同步
scripts/
  backup_database.ps1    # 数据库备份脚本
  service_control.ps1    # 服务控制脚本
  install_startup_task.ps1   # 安装开机自启任务
  uninstall_startup_task.ps1 # 卸载开机自启任务
tests/                   # pytest 测试套件
docs/
  feishu_setup.md        # 飞书机器人接入指南
  feishu_bitable_setup.md # 多维表格配置指南
init_db.py               # 数据库初始化脚本
.env.example             # 环境变量模板
requirements.txt         # Python 依赖
```

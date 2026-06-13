# 智账 Pro

本地个人账本：Streamlit 可视化 + SQLite 存储 + 邮件日报。

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

| 变量 | 说明 | 必填 |
| --- | --- | --- |
| `FINANCE_DB_FILE` | 数据库文件路径，默认 `my_account_book.db` | 否 |
| `FINANCE_MONTHLY_BUDGET` | 月度预算（元），默认 2000 | 否 |
| `FINANCE_MAIL_HOST` | SMTP 服务器，默认 `smtp.qq.com` | 邮件功能需要 |
| `FINANCE_MAIL_USER` | 发件邮箱 | 邮件功能需要 |
| `FINANCE_MAIL_PASS` | 邮箱授权码（非登录密码） | 邮件功能需要 |
| `FINANCE_MAIL_RECEIVERS` | 收件人，多个用逗号分隔 | 邮件功能需要 |

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

普通记账、删除和修改会先返回飞书确认卡片，确认后才写入 SQLite。AI 只负责把自然语言解析为结构化动作；金额、日期、分类、用户归属和最终写入都由本地 Python 校验。未配置 AI 或请求失败时会自动使用本地解析器。

```powershell
.\scripts\backup_database.ps1
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start_all.bat
```

配置步骤见：

- [飞书机器人接入指南](docs/feishu_setup.md)
- [飞书多维表格配置](docs/feishu_bitable_setup.md)

## 项目结构

```
finance_tracker/
  app.py              # Streamlit 界面
  ledger.py           # 核心记账逻辑与数据库
  email_service.py    # 邮件日报生成与发送
  scheduler.py        # 后台定时任务调度
  account_ops.py      # 命令行工具
  service_runner.py   # 进程管理
  config.py           # 环境变量加载
init_db.py            # 数据库初始化脚本
.env.example          # 环境变量模板
requirements.txt      # Python 依赖
```

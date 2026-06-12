import datetime
import io
import os
import smtplib
import sys
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

import markdown
import pandas as pd

MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from config import load_env_file
from ledger import DB_FILE, MONTHLY_BUDGET, SPECIAL_TAG_RULES, load_transactions


def get_mail_settings():
    load_env_file(override=True)
    mail_host = os.getenv("FINANCE_MAIL_HOST", "smtp.qq.com")
    mail_user = os.getenv("FINANCE_MAIL_USER", "")
    mail_pass = os.getenv("FINANCE_MAIL_PASS", "")
    receivers = [
        item.strip()
        for item in os.getenv("FINANCE_MAIL_RECEIVERS", mail_user).split(",")
        if item.strip()
    ]
    return mail_host, mail_user, mail_pass, receivers


def get_mail_config_status():
    mail_host, mail_user, mail_pass, receivers = get_mail_settings()
    missing = []
    if not mail_user:
        missing.append("FINANCE_MAIL_USER")
    if not mail_pass:
        missing.append("FINANCE_MAIL_PASS")
    if not receivers:
        missing.append("FINANCE_MAIL_RECEIVERS")

    return {
        "ready": not missing,
        "missing": missing,
        "host": mail_host,
        "user": mail_user,
        "has_password": bool(mail_pass),
        "receivers": receivers,
    }


def get_data_for_date(target_date):
    df = load_transactions()
    if df.empty:
        return None
    return df[df["date"].dt.date <= target_date].copy()


def generate_report_content(df, target_date):
    if isinstance(target_date, str):
        target_date = datetime.datetime.strptime(target_date, "%Y-%m-%d").date()
    elif isinstance(target_date, datetime.datetime):
        target_date = target_date.date()

    df = df.copy()
    if df.empty:
        return f"# 财务分析报告\n\n> 报告日：{target_date}  \n> 数据口径：截至报告日的本地账本\n\n暂无账本数据。"

    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    df["date_only"] = df["date"].dt.date
    df = df[df["date_only"] <= target_date].copy()
    if df.empty:
        return f"# 财务分析报告\n\n> 报告日：{target_date}  \n> 数据口径：截至报告日的本地账本\n\n报告日前暂无账本数据。"

    target_ts = pd.Timestamp(target_date)
    month_start = target_ts.replace(day=1).date()
    last_month_end = month_start - datetime.timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    last_month_same_end = last_month_start.replace(day=min(target_date.day, last_month_end.day))

    day_df = df[df["date_only"] == target_date]
    yest_date = target_date - datetime.timedelta(days=1)
    yest_df = df[df["date_only"] == yest_date]
    month_df = df[(df["date_only"] >= month_start) & (df["date_only"] <= target_date)]
    last_month_df = df[(df["date_only"] >= last_month_start) & (df["date_only"] <= last_month_same_end)]
    year_df = df[(df["date"].dt.year == target_date.year) & (df["date_only"] <= target_date)]

    day_exp = _sum(day_df, "支出")
    day_inc = _sum(day_df, "收入")
    yest_exp = _sum(yest_df, "支出")
    month_exp = _sum(month_df, "支出")
    month_inc = _sum(month_df, "收入")
    last_month_exp = _sum(last_month_df, "支出")
    last_month_inc = _sum(last_month_df, "收入")
    year_exp = _sum(year_df, "支出")
    year_inc = _sum(year_df, "收入")

    diff_pct = ((day_exp - yest_exp) / yest_exp * 100) if yest_exp else 0
    month_exp_diff = month_exp - last_month_exp
    month_inc_diff = month_inc - last_month_inc
    budget_pct = (month_exp / MONTHLY_BUDGET * 100) if MONTHLY_BUDGET else 0
    budget_remain = MONTHLY_BUDGET - month_exp
    month_days = max(target_date.day, 1)
    avg_daily_exp = month_exp / month_days
    year_balance = year_inc - year_exp
    month_balance = month_inc - month_exp

    day_detail = _category_table(day_df[day_df["type"] == "支出"], empty="今日无支出。")
    month_exp_detail = _category_table(month_df[month_df["type"] == "支出"], empty="本月暂无支出。", top=8)
    month_inc_detail = _category_table(month_df[month_df["type"] == "收入"], empty="本月暂无收入。", top=6)
    year_exp_detail = _category_table(year_df[year_df["type"] == "支出"], empty="今年暂无支出。", top=8)
    year_inc_detail = _category_table(year_df[year_df["type"] == "收入"], empty="今年暂无收入。", top=8)
    recent_rows = _recent_lines(day_df)
    budget_note = _budget_note(budget_pct, budget_remain)
    budget_level = _budget_level(budget_pct)
    top_month_category = _top_category(month_df[month_df["type"] == "支出"])
    largest_month_txn = _largest_transaction(month_df[month_df["type"] == "支出"])
    summary_lines = _executive_summary(
        day_exp=day_exp,
        yest_exp=yest_exp,
        month_exp=month_exp,
        month_inc=month_inc,
        budget_pct=budget_pct,
        budget_remain=budget_remain,
        avg_daily_exp=avg_daily_exp,
        top_category=top_month_category,
        largest_txn=largest_month_txn,
    )
    special_sections = _special_tag_sections(df, target_date)

    return f"""
# 财务分析报告

> 报告日：{target_date}  
> 数据口径：截至报告日的本地账本  
> 生成方式：本地规则统计，不调用外部 AI

## 1. 结论摘要

{summary_lines}

## 2. 关键指标

| 指标 | 数值 |
| --- | ---: |
| 今日支出 | ¥{day_exp:.2f} |
| 今日收入 | ¥{day_inc:.2f} |
| 本月累计支出 | ¥{month_exp:.2f} |
| 本月累计收入 | ¥{month_inc:.2f} |
| 本月结余 | ¥{month_balance:.2f} |
| 本月日均支出 | ¥{avg_daily_exp:.2f} |
| 年度累计支出 | ¥{year_exp:.2f} |
| 年度累计收入 | ¥{year_inc:.2f} |
| 年度结余 | ¥{year_balance:.2f} |

## 3. 今日复盘

| 指标 | 数值 |
| --- | ---: |
| 昨日支出 | ¥{yest_exp:.2f} |
| 今日较昨日 | {_change_text(day_exp, yest_exp, diff_pct)} |

{day_detail}

{recent_rows}

## 4. 本月表现

| 项目 | 本月至今 | 上月同期 | 差额 |
| --- | ---: | ---: | ---: |
| 支出 | ¥{month_exp:.2f} | ¥{last_month_exp:.2f} | ¥{month_exp_diff:+.2f} |
| 收入 | ¥{month_inc:.2f} | ¥{last_month_inc:.2f} | ¥{month_inc_diff:+.2f} |

### 支出结构

{month_exp_detail}

### 收入结构

{month_inc_detail}

## 5. 预算状态

| 指标 | 数值 |
| --- | ---: |
| 月度预算 | ¥{MONTHLY_BUDGET:.2f} |
| 已使用 | {budget_pct:.1f}% |
| 剩余额度 | ¥{budget_remain:.2f} |
| 状态 | {budget_level} |

`{_progress_bar(budget_pct)}` {budget_pct:.1f}%

> {budget_note}

## 6. 年度视角

### 年度支出结构

{year_exp_detail}

### 年度收入结构

{year_inc_detail}

{special_sections}
""".strip()


def send_email_task(report_date_str):
    if isinstance(report_date_str, str):
        target_date = datetime.datetime.strptime(report_date_str, "%Y-%m-%d").date()
    elif isinstance(report_date_str, datetime.datetime):
        target_date = report_date_str.date()
    else:
        target_date = report_date_str

    mail_host, mail_user, mail_pass, receivers = get_mail_settings()

    if not mail_user or not mail_pass or not receivers:
        return False, "邮箱未配置：请设置 FINANCE_MAIL_USER、FINANCE_MAIL_PASS、FINANCE_MAIL_RECEIVERS"

    df = get_data_for_date(target_date)
    if df is None:
        return False, f"数据库无数据：{DB_FILE}"

    report_md = generate_report_content(df, target_date)
    msg = MIMEMultipart()
    msg["Subject"] = Header(f"财务分析报告: {target_date}", "utf-8")
    msg["From"] = formataddr(["财务账本", mail_user])
    msg["To"] = Header(",".join(receivers), "utf-8")

    html_content = markdown.markdown(report_md, extensions=["tables"])
    css_style = """
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {margin: 0; padding: 0; background: #f6f8fb; color: #1f2937; font-family: "Microsoft YaHei", Arial, sans-serif;}
        .report-shell {max-width: 760px; margin: 0 auto; padding: 22px 16px 30px; background: #ffffff;}
        h1 {font-size: 26px; line-height: 1.25; color: #111827; margin: 0 0 14px;}
        h2 {font-size: 19px; color: #1f2937; margin: 28px 0 12px; padding-bottom: 6px; border-bottom: 1px solid #e5e7eb;}
        h3 {font-size: 16px; color: #374151; margin: 20px 0 10px;}
        p, li {font-size: 14px; line-height: 1.75;}
        table {border-collapse: collapse; width: 100%; margin: 12px 0 18px; font-size: 14px;}
        td, th {border: 1px solid #e5e7eb; padding: 9px 10px; text-align: left; vertical-align: top;}
        th {background-color: #f8fafc; color: #374151;}
        blockquote {margin: 12px 0 18px; padding: 10px 12px; border-left: 4px solid #2563eb; background: #eff6ff; color: #374151;}
        code {background: #f3f4f6; border-radius: 4px; padding: 2px 4px;}
        @media screen and (max-width: 600px) {
            .report-shell {padding: 18px 12px;}
            h1 {font-size: 22px;}
            h2 {font-size: 18px;}
            table {font-size: 13px;}
            td, th {padding: 8px 7px;}
        }
    </style>
    """
    msg.attach(MIMEText(f"<html><head>{css_style}</head><body><main class=\"report-shell\">{html_content}</main></body></html>", "html", "utf-8"))

    _attach_excel(msg, df, target_date)

    try:
        smtp = smtplib.SMTP_SSL(mail_host, 465, timeout=30)
        smtp.login(mail_user, mail_pass)
        smtp.sendmail(mail_user, receivers, msg.as_string())
        smtp.quit()
        return True, "发送成功"
    except Exception as exc:
        return False, f"发送失败：{exc}"


def _sum(df, txn_type):
    return df[df["type"] == txn_type]["amount"].sum()


def _money(value):
    return f"¥{float(value):.2f}"


def _md(value):
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _category_lines(df, empty, top=None):
    if df.empty:
        return empty
    grouped = df.groupby("category")["amount"].sum().sort_values(ascending=False)
    if top:
        grouped = grouped.head(top)
    return "\n".join(f"- {category}: ¥{amount:.2f}" for category, amount in grouped.items())


def _category_table(df, empty, top=None):
    if df.empty:
        return empty

    grouped = df.groupby("category")["amount"].sum().sort_values(ascending=False)
    total = grouped.sum()
    if top:
        grouped = grouped.head(top)

    lines = [
        "| 类别 | 金额 | 占比 |",
        "| --- | ---: | ---: |",
    ]
    for category, amount in grouped.items():
        ratio = amount / total * 100 if total else 0
        lines.append(f"| {_md(category)} | {_money(amount)} | {ratio:.1f}% |")
    return "\n".join(lines)


def _recent_lines(df):
    if df.empty:
        return ""
    lines = []
    for _, row in df.sort_values("amount", ascending=False).head(10).iterrows():
        desc = row.get("description") or row.get("category") or "未命名"
        sign = "+" if row.get("type") == "收入" else "-"
        lines.append(
            f"| {_md(row.get('type'))} | {_md(row.get('category'))} | {sign}{_money(row.get('amount', 0))} | {_md(desc)} |"
        )
    return "### 今日明细\n\n| 类型 | 类别 | 金额 | 说明 |\n| --- | --- | ---: | --- |\n" + "\n".join(lines)


def _top_category(df):
    if df.empty:
        return "暂无"
    grouped = df.groupby("category")["amount"].sum().sort_values(ascending=False)
    return f"{grouped.index[0]}（{_money(grouped.iloc[0])}）"


def _largest_transaction(df):
    if df.empty:
        return "暂无"
    row = df.sort_values("amount", ascending=False).iloc[0]
    desc = row.get("description") or row.get("category") or "未命名"
    return f"{row['date_only']} {_md(desc)} {_money(row.get('amount', 0))}"


def _executive_summary(day_exp, yest_exp, month_exp, month_inc, budget_pct, budget_remain, avg_daily_exp, top_category, largest_txn):
    balance = month_inc - month_exp
    lines = [
        f"- 今日支出 {_money(day_exp)}，{_day_change_sentence(day_exp, yest_exp)}。",
        f"- 本月至今支出 {_money(month_exp)}，收入 {_money(month_inc)}，结余 {_money(balance)}。",
        f"- 预算已使用 {budget_pct:.1f}%，剩余额度 {_money(budget_remain)}，当前状态：{_budget_level(budget_pct)}。",
        f"- 本月日均支出 {_money(avg_daily_exp)}，最大支出类别是 {top_category}。",
    ]
    if largest_txn != "暂无":
        lines.append(f"- 本月最大单笔支出：{largest_txn}。")
    return "\n".join(lines)


def _day_change_sentence(day_exp, yest_exp):
    if not yest_exp:
        return "昨日无支出可对比" if day_exp else "今日与昨日都无支出"
    diff = day_exp - yest_exp
    pct = diff / yest_exp * 100
    if abs(diff) < 0.01:
        return "与昨日基本持平"
    direction = "增加" if diff > 0 else "减少"
    return f"较昨日{direction} {_money(abs(diff))}（{pct:+.1f}%）"


def _change_text(day_exp, yest_exp, diff_pct):
    if not yest_exp:
        return "昨日无支出" if day_exp else "持平"
    diff = day_exp - yest_exp
    return f"{_money(diff)}（{diff_pct:+.1f}%）"


def _budget_note(budget_pct, budget_remain):
    if budget_pct >= 100:
        return "本月预算已经用完，后续支出建议先看刚需。"
    if budget_pct >= 80:
        return "本月预算使用较高，建议关注大额和非必要消费。"
    if budget_pct >= 50:
        return "预算使用处在中段，可以继续保持节奏。"
    return "预算压力较低，目前节奏比较从容。"


def _budget_level(budget_pct):
    if budget_pct >= 100:
        return "已超预算"
    if budget_pct >= 80:
        return "偏紧"
    if budget_pct >= 50:
        return "正常"
    return "宽松"


def _progress_bar(percent, width=12):
    clamped = max(0, min(float(percent), 100))
    filled = round(clamped / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _special_tag_sections(df, target_date):
    sections = []
    if "tags" not in df.columns:
        return ""

    tags_series = df["tags"].fillna("").astype(str)
    for rule in SPECIAL_TAG_RULES:
        report_from = datetime.datetime.strptime(rule["report_from"], "%Y-%m-%d").date()
        if target_date < report_from:
            continue

        tag = rule["tag"]
        start_date = datetime.datetime.strptime(rule["start_date"], "%Y-%m-%d").date()
        end_date = datetime.datetime.strptime(rule["end_date"], "%Y-%m-%d").date()
        tag_df = df[
            tags_series.str.contains(tag, regex=False)
            & (df["type"] == rule.get("type", "支出"))
            & (df["date_only"] >= start_date)
            & (df["date_only"] <= min(target_date, end_date))
        ].copy()

        title = rule.get("title", tag)
        if tag_df.empty:
            sections.append(
                f"""
## 7. {title}专项

| 指标 | 数值 |
| --- | ---: |
| 标签 | {tag} |
| 旅程日期 | {rule["start_date"]} 至 {rule["end_date"]} |
| 已记录支出 | ¥0.00 |

当前暂无已标记的专项支出记录。
""".strip()
            )
            continue

        total = tag_df["amount"].sum()
        days = max((end_date - start_date).days + 1, 1)
        recorded_days = tag_df["date_only"].nunique()
        category_lines = _category_table(tag_df, empty="暂无分类")
        day_lines = _daily_lines(tag_df)
        top_lines = _top_transaction_lines(tag_df)

        sections.append(
            f"""
## 7. {title}专项

| 指标 | 数值 |
| --- | ---: |
| 标签 | {tag} |
| 旅程日期 | {rule["start_date"]} 至 {rule["end_date"]} |
| 已记录支出 | ¥{total:.2f} |
| 有记录天数 | {recorded_days} / {days} 天 |
| 日均支出 | ¥{(total / days):.2f} |
| 最大支出类别 | {_top_category(tag_df)} |

### 专项支出结构

{category_lines}

### 每日支出

{day_lines}

### 大额明细

{top_lines}
""".strip()
        )

    return "\n\n".join(sections)


def _daily_lines(df):
    if df.empty:
        return "暂无记录"
    daily = df.groupby("date_only")["amount"].sum().sort_index()
    lines = ["| 日期 | 金额 |", "| --- | ---: |"]
    for date, amount in daily.items():
        lines.append(f"| {date} | {_money(amount)} |")
    return "\n".join(lines)


def _top_transaction_lines(df, top=8):
    if df.empty:
        return "暂无记录"
    lines = []
    for _, row in df.sort_values("amount", ascending=False).head(top).iterrows():
        desc = row.get("description") or row.get("category") or "未命名"
        lines.append(f"| {row['date_only']} | {_md(row.get('category'))} | {_money(row.get('amount', 0))} | {_md(desc)} |")
    return "| 日期 | 类别 | 金额 | 说明 |\n| --- | --- | ---: | --- |\n" + "\n".join(lines)


def _attach_excel(msg, df, target_date):
    try:
        excel_io = io.BytesIO()
        export_df = df.copy()
        export_df["date"] = export_df["date"].dt.strftime("%Y-%m-%d")
        cols = ["date", "type", "category", "amount", "description", "tags"]
        valid_cols = [col for col in cols if col in export_df.columns]
        export_df[valid_cols].to_excel(excel_io, index=False, sheet_name="账单明细")
        excel_io.seek(0)

        attachment = MIMEApplication(excel_io.read())
        attachment.add_header("Content-Disposition", "attachment", filename=f"Bill_{target_date}.xlsx")
        msg.attach(attachment)
    except Exception as exc:
        print(f"附件生成失败，不影响邮件正文：{exc}")

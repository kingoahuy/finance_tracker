import datetime

try:
    from .transaction_service import get_month_summary, get_today_summary
except ImportError:
    from transaction_service import get_month_summary, get_today_summary


def build_daily_report_card(target_date=None):
    target_date = target_date or datetime.date.today()
    today = get_today_summary(target_date)
    month = get_month_summary(target_date)
    top_lines = (
        "\n".join(
            f"{index}. {item['category']} ¥{item['amount']:.2f}"
            for index, item in enumerate(month["top_categories"], 1)
        )
        or "暂无支出"
    )
    transaction_lines = _today_transaction_lines(today["transactions"])
    reminder = _spending_reminder(month["budget_usage"])
    return {
        "config": {"wide_screen_mode": False},
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": f"财务日报｜{today['date']}",
            },
        },
        "elements": [
            {
                "tag": "markdown",
                "content": (
                    "**今日**\n"
                    f"支出 **¥{today['expense']:.2f}**　"
                    f"收入 **¥{today['income']:.2f}**　"
                    f"共 **{today['count']} 笔**"
                ),
            },
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": (
                    "**本月概览**\n"
                    f"支出 **¥{month['expense']:.2f}**\n"
                    f"收入 **¥{month['income']:.2f}**\n"
                    f"结余 **¥{month['balance']:.2f}**\n"
                    f"预算 {_progress_bar(month['budget_usage'])} "
                    f"**{month['budget_usage']:.1f}%**"
                ),
            },
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": f"**本月支出前三**\n{top_lines}",
            },
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": f"**今日明细**\n{transaction_lines}",
            },
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": reminder},
                ],
            },
        ],
    }


def build_daily_report_text(target_date=None):
    target_date = target_date or datetime.date.today()
    today = get_today_summary(target_date)
    month = get_month_summary(target_date)
    return (
        f"财务日报｜{today['date']}\n"
        f"今日：支出 ¥{today['expense']:.2f}，"
        f"收入 ¥{today['income']:.2f}，共 {today['count']} 笔\n"
        f"本月：支出 ¥{month['expense']:.2f}，"
        f"收入 ¥{month['income']:.2f}，"
        f"结余 ¥{month['balance']:.2f}\n"
        f"预算使用率：{month['budget_usage']:.1f}%\n"
        f"{_spending_reminder(month['budget_usage'])}"
    )


def _today_transaction_lines(records):
    if not records:
        return "今日暂无流水。"
    lines = []
    for row in records[:5]:
        description = str(row.get("description") or row.get("category") or "未命名")
        if len(description) > 16:
            description = description[:15] + "…"
        lines.append(
            f"• {description}　{row.get('type', '')} "
            f"**¥{float(row.get('amount') or 0):.2f}**"
        )
    if len(records) > 5:
        lines.append(f"• 另有 {len(records) - 5} 笔，请发送“今日账单”查看")
    return "\n".join(lines)


def _progress_bar(usage):
    filled = max(0, min(10, round(float(usage or 0) / 10)))
    return "■" * filled + "□" * (10 - filled)


def _spending_reminder(budget_usage):
    if budget_usage >= 100:
        return "预算已超支，请优先检查非刚需消费。"
    if budget_usage >= 80:
        return "预算使用率已超过 80%，建议控制后续支出。"
    return "预算使用处于可控范围，请继续保持记录。"

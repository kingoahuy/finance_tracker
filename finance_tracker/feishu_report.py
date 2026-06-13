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
    reminder = _spending_reminder(month["budget_usage"])
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": f"智账 Pro 财务日报｜{today['date']}"},
        },
        "elements": [
            {
                "tag": "markdown",
                "content": (
                    f"**今日支出：** ¥{today['expense']:.2f}\n"
                    f"**今日收入：** ¥{today['income']:.2f}\n"
                    f"**本月支出：** ¥{month['expense']:.2f}\n"
                    f"**本月收入：** ¥{month['income']:.2f}\n"
                    f"**本月结余：** ¥{month['balance']:.2f}\n"
                    f"**预算使用率：** {month['budget_usage']:.1f}%"
                ),
            },
            {"tag": "hr"},
            {"tag": "markdown", "content": f"**支出分类前三**\n{top_lines}"},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": reminder}]},
        ],
    }


def _spending_reminder(budget_usage):
    if budget_usage >= 100:
        return "预算已超支，请优先检查非刚需消费。"
    if budget_usage >= 80:
        return "预算使用率已超过 80%，建议控制后续支出。"
    return "预算使用处于可控范围，请继续保持记录。"

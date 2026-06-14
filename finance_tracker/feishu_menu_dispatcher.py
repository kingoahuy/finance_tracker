import datetime

try:
    from .analytics import (
        get_budget_warning,
        get_category_expense_summary,
        get_finance_overview,
    )
    from .bitable_sync import sync_pending_transactions
    from .transaction_service import generate_daily_report
except ImportError:
    from analytics import (
        get_budget_warning,
        get_category_expense_summary,
        get_finance_overview,
    )
    from bitable_sync import sync_pending_transactions
    from transaction_service import generate_daily_report


MENU_EVENT_TYPE = "application.bot.menu_v6"

MENU_HELP_TEXT = """智账 Pro 菜单

- 今日账单：查看今日收入、支出和结余
- 本月概览：查看本月收支和结余率
- 分类排行：查看本月支出分类前五
- 预算预警：查看接近超支和已超支分类
- 生成日报：生成今日财务日报
- 同步刷新：同步待处理流水到飞书多维表格
- 使用帮助：查看菜单说明

也可以直接发送自然语言记账，例如：午饭 25 元。"""


def handle_menu_event(event_key, user_id):
    """Dispatch a Feishu custom-menu event and return its reply text."""
    handler = MENU_HANDLERS.get(str(event_key or "").strip(), _handle_unknown)
    try:
        return str(handler(str(user_id or "")) or "菜单操作已完成。")
    except Exception:
        return "菜单处理失败，请稍后重试。"


def _today_bill(_user_id):
    today = datetime.date.today()
    summary = get_finance_overview(today, today)
    lines = [
        f"今日账单｜{today.isoformat()}",
        f"收入：¥{summary['total_income']:.2f}",
        f"支出：¥{summary['total_expense']:.2f}",
        f"结余：¥{summary['net_income']:.2f}",
        f"交易笔数：{summary['transaction_count']}",
    ]
    return "\n".join(lines)


def _month_summary(_user_id):
    today = datetime.date.today()
    start_date = today.replace(day=1)
    summary = get_finance_overview(start_date, today)
    lines = [
        f"本月概览｜{today.strftime('%Y-%m')}",
        f"收入：¥{summary['total_income']:.2f}",
        f"支出：¥{summary['total_expense']:.2f}",
        f"结余：¥{summary['net_income']:.2f}",
        f"结余率：{summary['savings_rate']:.2f}%",
        f"交易笔数：{summary['transaction_count']}",
    ]
    return "\n".join(lines)


def _category_rank(_user_id):
    month = datetime.date.today().strftime("%Y-%m")
    rows = get_category_expense_summary(month)[:5]
    if not rows:
        return f"分类排行｜{month}\n本月暂无支出记录。"
    lines = [f"分类排行｜{month}"]
    lines.extend(
        f"{index}. {row['category']}：¥{row['amount']:.2f}（{row['share']:.1f}%）"
        for index, row in enumerate(rows, 1)
    )
    return "\n".join(lines)


def _budget_warning(_user_id):
    month = datetime.date.today().strftime("%Y-%m")
    rows = get_budget_warning(month)
    risky = [row for row in rows if row["status"] != "正常"]
    if not risky:
        return f"预算预警｜{month}\n当前各分类预算状态正常。"
    risky.sort(key=lambda row: row["usage_rate"], reverse=True)
    lines = [f"预算预警｜{month}"]
    lines.extend(
        (
            f"{row['status']}：{row['category']}，"
            f"已用 ¥{row['used']:.2f}，使用率 {row['usage_rate']:.1f}%"
        )
        for row in risky[:5]
    )
    return "\n".join(lines)


def _daily_report(_user_id):
    return generate_daily_report()


def _sync_refresh(_user_id):
    result = sync_pending_transactions()
    succeeded = int(result.get("succeeded", 0) or 0)
    failed = int(result.get("failed", 0) or 0)
    processed = int(result.get("processed", succeeded + failed) or 0)
    if result.get("success"):
        return (
            "同步刷新完成。\n"
            f"已处理：{processed}\n"
            f"成功：{succeeded}\n"
            f"失败：{failed}"
        )
    message = str(result.get("message") or "同步失败，请到设置页检查配置。")
    return (
        "同步刷新失败。\n"
        f"已处理：{processed}\n"
        f"成功：{succeeded}\n"
        f"失败：{failed}\n"
        f"原因：{message[:160]}"
    )


def _help(_user_id):
    return MENU_HELP_TEXT


def _handle_unknown(_user_id):
    return "未识别这个菜单操作。\n\n" + MENU_HELP_TEXT


MENU_HANDLERS = {
    "today_bill": _today_bill,
    "month_summary": _month_summary,
    "category_rank": _category_rank,
    "budget_warning": _budget_warning,
    "daily_report": _daily_report,
    "sync_refresh": _sync_refresh,
    "help": _help,
}


__all__ = [
    "MENU_EVENT_TYPE",
    "MENU_HANDLERS",
    "handle_menu_event",
]

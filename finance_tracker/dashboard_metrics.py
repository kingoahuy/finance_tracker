import calendar
import datetime
from collections import defaultdict, deque

try:
    from .advance_payment import has_personal_advance_tag
    from .ledger import MONTHLY_BUDGET, connect, init_db
except ImportError:
    from advance_payment import has_personal_advance_tag
    from ledger import MONTHLY_BUDGET, connect, init_db


DAILY_DASHBOARD_TABLE_NAME = "看板日指标"
DAILY_DASHBOARD_VERSION = "daily-dashboard-v1"
WEEKDAYS_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 飞书字段类型：1 文本、2 数字、5 日期、7 复选框。
DAILY_DASHBOARD_FIELD_SPECS = (
    {"key": "metric_date_key", "label": "指标日期", "bitable_type": 1},
    {"key": "metric_date", "label": "日期", "bitable_type": 5},
    {"key": "date_year", "label": "年", "bitable_type": 2},
    {"key": "date_year_month", "label": "年-月", "bitable_type": 1},
    {"key": "date_month", "label": "月", "bitable_type": 2},
    {"key": "date_day", "label": "日", "bitable_type": 2},
    {"key": "date_weekday", "label": "星期", "bitable_type": 1},
    {"key": "date_quarter", "label": "季度", "bitable_type": 1},
    {"key": "is_latest", "label": "是否最新", "bitable_type": 7},
    {"key": "is_current_month", "label": "是否本月", "bitable_type": 7},
    {"key": "is_current_year", "label": "是否本年", "bitable_type": 7},
    {"key": "is_month_observation", "label": "是否月度观察点", "bitable_type": 7},
    {"key": "mtd_scope_complete", "label": "MTD口径完整", "bitable_type": 7},
    {"key": "ytd_scope_complete", "label": "YTD口径完整", "bitable_type": 7},
    {"key": "month_elapsed_days", "label": "本月已过天数", "bitable_type": 2},
    {"key": "year_elapsed_days", "label": "本年已过天数", "bitable_type": 2},
    {"key": "year_elapsed_months", "label": "本年已过月数", "bitable_type": 2},
    {"key": "daily_income", "label": "当日收入", "bitable_type": 2},
    {"key": "daily_expense", "label": "当日支出", "bitable_type": 2},
    {"key": "daily_net", "label": "当日净额", "bitable_type": 2},
    {"key": "daily_transaction_count", "label": "当日交易笔数", "bitable_type": 2},
    {"key": "mtd_income", "label": "MTD收入", "bitable_type": 2},
    {"key": "mtd_expense", "label": "MTD支出", "bitable_type": 2},
    {"key": "mtd_net", "label": "MTD净额", "bitable_type": 2},
    {"key": "mtd_daily_avg_income", "label": "MTD日均收入", "bitable_type": 2},
    {"key": "mtd_daily_avg_expense", "label": "MTD日均支出", "bitable_type": 2},
    {"key": "mtd_savings_rate", "label": "MTD储蓄率", "bitable_type": 2},
    {"key": "ytd_income", "label": "YTD收入", "bitable_type": 2},
    {"key": "ytd_expense", "label": "YTD支出", "bitable_type": 2},
    {"key": "ytd_net", "label": "YTD净额", "bitable_type": 2},
    {"key": "ytd_daily_avg_income", "label": "YTD日均收入", "bitable_type": 2},
    {"key": "ytd_daily_avg_expense", "label": "YTD日均支出", "bitable_type": 2},
    {"key": "ytd_monthly_avg_income", "label": "YTD月均收入", "bitable_type": 2},
    {"key": "ytd_monthly_avg_expense", "label": "YTD月均支出", "bitable_type": 2},
    {"key": "ytd_savings_rate", "label": "YTD储蓄率", "bitable_type": 2},
    {"key": "rolling_7d_avg_expense", "label": "近7日日均支出", "bitable_type": 2},
    {"key": "rolling_30d_avg_expense", "label": "近30日日均支出", "bitable_type": 2},
    {"key": "monthly_budget", "label": "月预算", "bitable_type": 2},
    {"key": "mtd_budget_usage", "label": "MTD预算使用率", "bitable_type": 2},
    {"key": "month_time_progress", "label": "月度时间进度", "bitable_type": 2},
    {"key": "budget_progress_gap", "label": "预算进度偏差", "bitable_type": 2},
    {"key": "budget_remaining", "label": "预算剩余", "bitable_type": 2},
    {"key": "projected_month_expense", "label": "预计月末支出", "bitable_type": 2},
    {"key": "daily_advance_expense", "label": "当日垫付支出", "bitable_type": 2},
    {"key": "daily_advance_reimbursement", "label": "当日垫付回款", "bitable_type": 2},
    {"key": "advance_balance", "label": "垫付余额", "bitable_type": 2},
    {"key": "data_version", "label": "数据版本", "bitable_type": 1},
    {"key": "refreshed_at", "label": "刷新时间", "bitable_type": 5},
)

DAILY_DASHBOARD_FIELD_LABELS = {
    item["key"]: item["label"] for item in DAILY_DASHBOARD_FIELD_SPECS
}
DAILY_DASHBOARD_BITABLE_TYPES = {
    item["label"]: item["bitable_type"] for item in DAILY_DASHBOARD_FIELD_SPECS
}


def build_daily_dashboard_rows(as_of_date=None):
    """Build one calendar-day snapshot row for Feishu dashboard metrics."""
    as_of = _as_date(as_of_date or datetime.date.today())
    transactions = _load_active_transactions(as_of)
    data_start = min(
        (item["date"] for item in transactions),
        default=as_of,
    )
    daily = defaultdict(_empty_daily_amounts)
    for item in transactions:
        bucket = daily[item["date"]]
        amount = item["amount"]
        if has_personal_advance_tag(item["tags"]):
            if item["type"] == "支出":
                bucket["advance_expense"] += amount
            elif item["type"] == "收入":
                bucket["advance_reimbursement"] += amount
            continue
        if item["type"] == "收入":
            bucket["income"] += amount
        elif item["type"] == "支出":
            bucket["expense"] += amount
        bucket["transaction_count"] += 1

    rows = []
    current_month = None
    current_year = None
    month_income = month_expense = 0.0
    year_income = year_expense = 0.0
    advance_balance = 0.0
    rolling_7d = deque(maxlen=7)
    rolling_30d = deque(maxlen=30)
    refreshed_at = datetime.datetime.now()

    for metric_date in _date_range(data_start, as_of):
        month_key = (metric_date.year, metric_date.month)
        if month_key != current_month:
            current_month = month_key
            month_income = month_expense = 0.0
        if metric_date.year != current_year:
            current_year = metric_date.year
            year_income = year_expense = 0.0

        amounts = daily[metric_date]
        month_income += amounts["income"]
        month_expense += amounts["expense"]
        year_income += amounts["income"]
        year_expense += amounts["expense"]
        advance_balance += (
            amounts["advance_expense"] - amounts["advance_reimbursement"]
        )
        rolling_7d.append(amounts["expense"])
        rolling_30d.append(amounts["expense"])

        days_in_month = calendar.monthrange(
            metric_date.year,
            metric_date.month,
        )[1]
        month_first = metric_date.replace(day=1)
        year_first = metric_date.replace(month=1, day=1)
        month_net = month_income - month_expense
        year_net = year_income - year_expense
        budget = float(MONTHLY_BUDGET or 0)
        budget_usage = _percent(month_expense, budget)
        time_progress = _percent(metric_date.day, days_in_month)
        quarter = (metric_date.month - 1) // 3 + 1

        rows.append(
            {
                "metric_date_key": metric_date.isoformat(),
                "metric_date": metric_date,
                "date_year": metric_date.year,
                "date_year_month": metric_date.strftime("%Y-%m"),
                "date_month": metric_date.month,
                "date_day": metric_date.day,
                "date_weekday": WEEKDAYS_CN[metric_date.weekday()],
                "date_quarter": f"{metric_date.year:04d}Q{quarter}",
                "is_latest": metric_date == as_of,
                "is_current_month": (
                    metric_date.year == as_of.year
                    and metric_date.month == as_of.month
                ),
                "is_current_year": metric_date.year == as_of.year,
                "is_month_observation": (
                    metric_date.day == days_in_month or metric_date == as_of
                ),
                "mtd_scope_complete": data_start <= month_first,
                "ytd_scope_complete": data_start <= year_first,
                "month_elapsed_days": metric_date.day,
                "year_elapsed_days": metric_date.timetuple().tm_yday,
                "year_elapsed_months": metric_date.month,
                "daily_income": _money(amounts["income"]),
                "daily_expense": _money(amounts["expense"]),
                "daily_net": _money(amounts["income"] - amounts["expense"]),
                "daily_transaction_count": int(amounts["transaction_count"]),
                "mtd_income": _money(month_income),
                "mtd_expense": _money(month_expense),
                "mtd_net": _money(month_net),
                "mtd_daily_avg_income": _money(month_income / metric_date.day),
                "mtd_daily_avg_expense": _money(month_expense / metric_date.day),
                "mtd_savings_rate": _percent(month_net, month_income),
                "ytd_income": _money(year_income),
                "ytd_expense": _money(year_expense),
                "ytd_net": _money(year_net),
                "ytd_daily_avg_income": _money(
                    year_income / metric_date.timetuple().tm_yday
                ),
                "ytd_daily_avg_expense": _money(
                    year_expense / metric_date.timetuple().tm_yday
                ),
                "ytd_monthly_avg_income": _money(
                    year_income / metric_date.month
                ),
                "ytd_monthly_avg_expense": _money(
                    year_expense / metric_date.month
                ),
                "ytd_savings_rate": _percent(year_net, year_income),
                "rolling_7d_avg_expense": _money(
                    sum(rolling_7d) / len(rolling_7d)
                ),
                "rolling_30d_avg_expense": _money(
                    sum(rolling_30d) / len(rolling_30d)
                ),
                "monthly_budget": _money(budget),
                "mtd_budget_usage": budget_usage,
                "month_time_progress": time_progress,
                "budget_progress_gap": _money(budget_usage - time_progress),
                "budget_remaining": _money(budget - month_expense),
                "projected_month_expense": _money(
                    month_expense / metric_date.day * days_in_month
                ),
                "daily_advance_expense": _money(amounts["advance_expense"]),
                "daily_advance_reimbursement": _money(
                    amounts["advance_reimbursement"]
                ),
                "advance_balance": _money(advance_balance),
                "data_version": DAILY_DASHBOARD_VERSION,
                "refreshed_at": refreshed_at,
            }
        )
    return rows


def daily_dashboard_sync_due(as_of_date=None):
    as_of = _as_date(as_of_date or datetime.date.today()).isoformat()
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM dashboard_sync_state WHERE key = ?",
            ("daily_dashboard_date",),
        ).fetchone()
    return not row or str(row[0] or "") != as_of


def mark_daily_dashboard_synced(as_of_date=None):
    as_of = _as_date(as_of_date or datetime.date.today()).isoformat()
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO dashboard_sync_state (key, value, updated_at)
            VALUES ('daily_dashboard_date', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (as_of,),
        )


def _load_active_transactions(as_of):
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT date, type, amount, tags
            FROM transactions
            WHERE COALESCE(status, 'active') = 'active'
              AND date IS NOT NULL
              AND substr(date, 1, 10) <= ?
            ORDER BY date ASC, rowid ASC
            """,
            (as_of.isoformat(),),
        ).fetchall()
    result = []
    for date_value, txn_type, amount, tags in rows:
        try:
            txn_date = _as_date(str(date_value)[:10])
        except ValueError:
            continue
        result.append(
            {
                "date": txn_date,
                "type": str(txn_type or ""),
                "amount": float(amount or 0),
                "tags": tags,
            }
        )
    return result


def _empty_daily_amounts():
    return {
        "income": 0.0,
        "expense": 0.0,
        "transaction_count": 0,
        "advance_expense": 0.0,
        "advance_reimbursement": 0.0,
    }


def _date_range(start_date, end_date):
    current = start_date
    while current <= end_date:
        yield current
        current += datetime.timedelta(days=1)


def _as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def _money(value):
    return round(float(value or 0), 2)


def _percent(numerator, denominator):
    denominator = float(denominator or 0)
    if not denominator:
        return 0.0
    return round(float(numerator or 0) / denominator * 100, 2)

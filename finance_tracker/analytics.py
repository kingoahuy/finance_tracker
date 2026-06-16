import calendar
import datetime
from collections import defaultdict

try:
    from .ledger import MONTHLY_BUDGET, connect, init_db
    from .tagging import clean_tags
except ImportError:
    from ledger import MONTHLY_BUDGET, connect, init_db
    from tagging import clean_tags


CATEGORY_BUDGET_WEIGHTS = {
    "餐饮": 0.30,
    "交通": 0.15,
    "购物": 0.15,
    "娱乐": 0.10,
    "居住": 0.15,
    "医疗": 0.05,
    "教育": 0.05,
    "人情": 0.03,
    "其他": 0.02,
}


def get_finance_overview(start_date=None, end_date=None):
    rows = _load_active_transactions(start_date, end_date)
    income_rows = [row for row in rows if row["type"] == "收入"]
    expense_rows = [row for row in rows if row["type"] == "支出"]
    total_income = _sum_amount(income_rows)
    total_expense = _sum_amount(expense_rows)
    net_income = total_income - total_expense
    largest = max((row["amount"] for row in expense_rows), default=0.0)
    category_totals = _group_amount(expense_rows, "category")
    largest_category = (
        max(category_totals, key=category_totals.get) if category_totals else ""
    )
    active_days = _date_span_days(rows, start_date, end_date)

    today = datetime.date.today()
    current_month = today.strftime("%Y-%m")
    month_rows = [row for row in _load_active_transactions() if row["month"] == current_month]
    month_income = _sum_amount(row for row in month_rows if row["type"] == "收入")
    month_expense = _sum_amount(row for row in month_rows if row["type"] == "支出")
    today_expense = _sum_amount(
        row
        for row in month_rows
        if row["type"] == "支出" and row["date"] == today.isoformat()
    )

    return {
        "total_income": _money(total_income),
        "total_expense": _money(total_expense),
        "net_income": _money(net_income),
        "savings_rate": _percent(net_income, total_income),
        "transaction_count": len(rows),
        "average_daily_expense": _money(total_expense / active_days if active_days else 0),
        "largest_expense": _money(largest),
        "largest_expense_category": largest_category,
        "current_month_income": _money(month_income),
        "current_month_expense": _money(month_expense),
        "current_month_balance": _money(month_income - month_expense),
        "today_expense": _money(today_expense),
    }


def get_monthly_trend():
    grouped = defaultdict(list)
    for row in _load_active_transactions():
        grouped[row["month"]].append(row)

    result = []
    for month in sorted(grouped):
        rows = grouped[month]
        income_rows = [row for row in rows if row["type"] == "收入"]
        expense_rows = [row for row in rows if row["type"] == "支出"]
        income = _sum_amount(income_rows)
        expense = _sum_amount(expense_rows)
        result.append(
            {
                "month": month,
                "income": _money(income),
                "expense": _money(expense),
                "net_income": _money(income - expense),
                "savings_rate": _percent(income - expense, income),
                "expense_count": len(expense_rows),
                "income_count": len(income_rows),
            }
        )
    return result


def get_category_expense_summary(month=None):
    target_month = _normalize_month(month)
    rows = _month_rows(target_month, "支出")
    total = _sum_amount(rows)
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["category"]].append(row)

    days = _days_in_month(target_month)
    return [
        {
            "month": target_month,
            "category": category,
            "amount": _money(_sum_amount(items)),
            "share": _percent(_sum_amount(items), total),
            "count": len(items),
            "average_daily_amount": _money(_sum_amount(items) / days if days else 0),
        }
        for category, items in sorted(
            grouped.items(),
            key=lambda item: _sum_amount(item[1]),
            reverse=True,
        )
    ]


def get_daily_expense_trend(month=None):
    target_month = _normalize_month(month)
    rows = _month_rows(target_month, "支出")
    by_date = defaultdict(list)
    for row in rows:
        by_date[row["date"]].append(row)

    result = []
    history = []
    for day in range(1, _days_in_month(target_month) + 1):
        date_value = f"{target_month}-{day:02d}"
        items = by_date.get(date_value, [])
        amount = _sum_amount(items)
        history.append(amount)
        result.append(
            {
                "date": date_value,
                "month": target_month,
                "expense": _money(amount),
                "expense_count": len(items),
                "moving_average_7d": _money(
                    sum(history[-7:]) / len(history[-7:])
                ),
            }
        )
    return result


def get_income_source_summary(month=None):
    target_month = _normalize_month(month)
    rows = _month_rows(target_month, "收入")
    total = _sum_amount(rows)
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["category"]].append(row)
    return [
        {
            "month": target_month,
            "income_category": category,
            "amount": _money(_sum_amount(items)),
            "share": _percent(_sum_amount(items), total),
            "count": len(items),
        }
        for category, items in sorted(
            grouped.items(),
            key=lambda item: _sum_amount(item[1]),
            reverse=True,
        )
    ]


def get_tag_summary(month=None):
    target_month = _normalize_month(month)
    grouped = defaultdict(list)
    for row in _month_rows(target_month, "支出"):
        for tag in clean_tags(row["tags"]):
            grouped[tag].append(row)

    return [
        {
            "month": target_month,
            "tag": tag,
            "amount": _money(_sum_amount(items)),
            "count": len(items),
            "related_categories": sorted({row["category"] for row in items}),
        }
        for tag, items in sorted(
            grouped.items(),
            key=lambda item: _sum_amount(item[1]),
            reverse=True,
        )
    ]


def get_need_vs_want_summary(month=None):
    return _binary_expense_summary(
        _normalize_month(month),
        "is_need",
        {1: "刚需", 0: "非刚需"},
    )


def get_fixed_vs_variable_summary(month=None):
    return _binary_expense_summary(
        _normalize_month(month),
        "is_fixed",
        {1: "固定支出", 0: "变动支出"},
    )


def get_top_expenses(month=None, limit=10):
    target_month = _normalize_month(month)
    safe_limit = max(1, min(int(limit), 100))
    rows = sorted(
        _month_rows(target_month, "支出"),
        key=lambda row: (row["amount"], row["date"], row["id"]),
        reverse=True,
    )
    return [
        {
            "month": target_month,
            "date": row["date"],
            "category": row["category"],
            "amount": _money(row["amount"]),
            "description": row["description"],
            "tags": clean_tags(row["tags"]),
        }
        for row in rows[:safe_limit]
    ]


def get_budget_warning(month=None):
    target_month = _normalize_month(month)
    used_by_category = _group_amount(_month_rows(target_month, "支出"), "category")
    categories = list(CATEGORY_BUDGET_WEIGHTS)
    for category in used_by_category:
        if category not in categories:
            categories.append(category)

    result = []
    for category in categories:
        weight = CATEGORY_BUDGET_WEIGHTS.get(category, 0)
        budget = float(MONTHLY_BUDGET) * weight
        used = used_by_category.get(category, 0.0)
        usage = used / budget * 100 if budget else (100.0 if used else 0.0)
        status = "已超支" if usage >= 100 else "接近超支" if usage >= 80 else "正常"
        result.append(
            {
                "month": target_month,
                "category": category,
                "budget": _money(budget),
                "used": _money(used),
                "remaining": _money(budget - used),
                "usage_rate": _money(usage),
                "status": status,
            }
        )
    return result


def generate_finance_insights(month=None):
    target_month = _normalize_month(month)
    categories = get_category_expense_summary(target_month)
    needs = get_need_vs_want_summary(target_month)
    top_expenses = get_top_expenses(target_month, limit=5)
    total_expense = _sum_amount(_month_rows(target_month, "支出"))
    total_income = _sum_amount(_month_rows(target_month, "收入"))
    leading = categories[0] if categories else None
    non_need = next((item for item in needs if item["type"] == "非刚需"), None)

    abnormal_threshold = max(
        (total_expense / max(1, sum(item["count"] for item in categories))) * 2,
        float(MONTHLY_BUDGET) * 0.20,
    )
    abnormal = [
        {
            "date": item["date"],
            "category": item["category"],
            "amount": item["amount"],
            "tags": item["tags"],
        }
        for item in top_expenses
        if item["amount"] >= abnormal_threshold
    ][:3]

    if not total_expense and not total_income:
        summary = f"{target_month} 暂无有效收支记录。"
    else:
        summary = (
            f"{target_month} 收入 ¥{total_income:.2f}，支出 ¥{total_expense:.2f}，"
            f"净收入 ¥{total_income - total_expense:.2f}。"
        )
    if non_need and non_need["share"] >= 40:
        saving_advice = "非刚需支出占比较高，建议优先压缩可替代的小额消费。"
    elif leading:
        saving_advice = f"可优先复核{leading['category']}支出，关注重复和低必要性项目。"
    else:
        saving_advice = "继续保持完整记账，积累更多数据后再优化预算。"

    warnings = get_budget_warning(target_month)
    risky = [item["category"] for item in warnings if item["status"] != "正常"]
    reminder = (
        "下月重点关注：" + "、".join(risky[:3]) + "。"
        if risky
        else "下月继续沿用当前预算节奏，并关注大额支出。"
    )
    return {
        "month": target_month,
        "summary": summary,
        "primary_expense_category": (
            {
                "category": leading["category"],
                "amount": leading["amount"],
                "share": leading["share"],
            }
            if leading
            else None
        ),
        "abnormal_expenses": abnormal,
        "saving_advice": saving_advice,
        "next_month_reminder": reminder,
    }


def _load_active_transactions(start_date=None, end_date=None):
    init_db()
    conditions = ["status = 'active'"]
    params = []
    if start_date:
        conditions.append("date >= ?")
        params.append(_as_date(start_date).isoformat())
    if end_date:
        conditions.append("date <= ?")
        params.append(_as_date(end_date).isoformat())

    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, date, type, category, amount, description, tags,
                   is_need, is_fixed
            FROM transactions
            WHERE {' AND '.join(conditions)}
            ORDER BY date, rowid
            """,
            params,
        ).fetchall()

    result = []
    for row in rows:
        try:
            date_value = _as_date(row[1]).isoformat()
        except (TypeError, ValueError):
            continue
        result.append(
            {
                "id": int(row[0] or 0),
                "date": date_value,
                "month": date_value[:7],
                "type": str(row[2] or "支出"),
                "category": str(row[3] or "其他"),
                "amount": float(row[4] or 0),
                "description": str(row[5] or ""),
                "tags": str(row[6] or ""),
                "is_need": int(bool(row[7])),
                "is_fixed": int(bool(row[8])),
            }
        )
    return result


def _month_rows(month, txn_type=None):
    rows = [row for row in _load_active_transactions() if row["month"] == month]
    if txn_type:
        rows = [row for row in rows if row["type"] == txn_type]
    return rows


def _binary_expense_summary(month, field, labels):
    rows = _month_rows(month, "支出")
    total = _sum_amount(rows)
    result = []
    for value in (1, 0):
        amount = _sum_amount(row for row in rows if row[field] == value)
        result.append(
            {
                "month": month,
                "type": labels[value],
                "amount": _money(amount),
                "share": _percent(amount, total),
            }
        )
    return result


def _group_amount(rows, key):
    grouped = defaultdict(float)
    for row in rows:
        grouped[row[key]] += row["amount"]
    return dict(grouped)


def _sum_amount(rows):
    return sum(float(row["amount"] or 0) for row in rows)


def _date_span_days(rows, start_date=None, end_date=None):
    dates = [_as_date(row["date"]) for row in rows]
    first = _as_date(start_date) if start_date else min(dates, default=None)
    last = _as_date(end_date) if end_date else max(dates, default=None)
    return (last - first).days + 1 if first and last and last >= first else 0


def _normalize_month(value=None):
    if value is None:
        return datetime.date.today().strftime("%Y-%m")
    text = str(value).strip()
    if len(text) >= 7:
        datetime.datetime.strptime(text[:7], "%Y-%m")
        return text[:7]
    raise ValueError("month must use YYYY-MM format.")


def _days_in_month(month):
    year, month_number = map(int, month.split("-"))
    return calendar.monthrange(year, month_number)[1]


def _as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _money(value):
    return round(float(value or 0), 2)


def _percent(numerator, denominator):
    return _money(numerator / denominator * 100) if denominator else 0.0

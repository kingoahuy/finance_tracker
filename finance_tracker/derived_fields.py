import datetime
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


DATA_VERSION = "derived-v2"
WEEKDAYS_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

DERIVED_FIELD_SPECS = (
    {"key": "date_year", "label": "年", "sqlite": "INTEGER", "bitable_type": 2},
    {"key": "date_year_month", "label": "年-月", "sqlite": "TEXT", "bitable_type": 1},
    {"key": "date_month", "label": "月", "sqlite": "INTEGER", "bitable_type": 2},
    {"key": "date_day", "label": "日", "sqlite": "INTEGER", "bitable_type": 2},
    {"key": "date_weekday", "label": "星期", "sqlite": "TEXT", "bitable_type": 1},
    {"key": "date_week_number", "label": "周数", "sqlite": "INTEGER", "bitable_type": 2},
    {"key": "date_quarter", "label": "季度", "sqlite": "TEXT", "bitable_type": 1},
    {"key": "income_amount", "label": "收入金额", "sqlite": "REAL", "bitable_type": 2},
    {"key": "expense_amount", "label": "支出金额", "sqlite": "REAL", "bitable_type": 2},
    {"key": "net_amount", "label": "净额", "sqlite": "REAL", "bitable_type": 2},
    {"key": "amount_bucket", "label": "金额区间", "sqlite": "TEXT", "bitable_type": 1},
    {"key": "tags_text", "label": "标签文本", "sqlite": "TEXT", "bitable_type": 1},
    {"key": "is_income", "label": "是否收入", "sqlite": "INTEGER DEFAULT 0", "bitable_type": 7},
    {"key": "is_expense", "label": "是否支出", "sqlite": "INTEGER DEFAULT 0", "bitable_type": 7},
    {"key": "is_active", "label": "是否有效", "sqlite": "INTEGER DEFAULT 1", "bitable_type": 7},
    {"key": "is_personal_advance", "label": "是否个人垫付", "sqlite": "INTEGER DEFAULT 0", "bitable_type": 7},
    {"key": "dashboard_income_amount", "label": "看板收入", "sqlite": "REAL", "bitable_type": 2},
    {"key": "dashboard_expense_amount", "label": "看板支出", "sqlite": "REAL", "bitable_type": 2},
    {"key": "dashboard_net_amount", "label": "看板净额", "sqlite": "REAL", "bitable_type": 2},
    {"key": "need_expense_amount", "label": "刚需支出", "sqlite": "REAL", "bitable_type": 2},
    {"key": "want_expense_amount", "label": "非刚需支出", "sqlite": "REAL", "bitable_type": 2},
    {"key": "fixed_expense_amount", "label": "固定支出", "sqlite": "REAL", "bitable_type": 2},
    {"key": "variable_expense_amount", "label": "变动支出", "sqlite": "REAL", "bitable_type": 2},
    {"key": "ledger_month", "label": "记账月份", "sqlite": "TEXT", "bitable_type": 1},
    {"key": "data_version", "label": "数据版本", "sqlite": "TEXT", "bitable_type": 1},
)

DERIVED_COLUMNS = tuple(item["key"] for item in DERIVED_FIELD_SPECS)
DERIVED_FIELD_LABELS = {
    item["key"]: item["label"] for item in DERIVED_FIELD_SPECS
}
DERIVED_SQLITE_DEFINITIONS = {
    item["key"]: item["sqlite"] for item in DERIVED_FIELD_SPECS
}
DERIVED_BITABLE_TYPES = {
    item["label"]: item["bitable_type"] for item in DERIVED_FIELD_SPECS
}


def enrich_transaction_fields(transaction):
    """Return a copy of transaction with stable derived dashboard fields."""
    result = dict(transaction or {})
    txn_date, date_error = _parse_date(result.get("date"))
    amount = _round_money(result.get("amount"))
    txn_type = str(result.get("type") or "支出").strip()
    status = str(result.get("status") or "active").strip() or "active"
    tags = clean_tags(result.get("tags"))

    if txn_date is None:
        result.update(
            {
                "date_year": None,
                "date_year_month": "",
                "date_month": None,
                "date_day": None,
                "date_weekday": "",
                "date_week_number": None,
                "date_quarter": "",
                "ledger_month": "",
            }
        )
    else:
        iso = txn_date.isocalendar()
        quarter = (txn_date.month - 1) // 3 + 1
        year_month = f"{txn_date.year:04d}-{txn_date.month:02d}"
        result.update(
            {
                "date_year": txn_date.year,
                "date_year_month": year_month,
                "date_month": txn_date.month,
                "date_day": txn_date.day,
                "date_weekday": WEEKDAYS_CN[txn_date.weekday()],
                "date_week_number": int(iso.week),
                "date_quarter": f"{txn_date.year:04d}Q{quarter}",
                "ledger_month": year_month,
            }
        )

    is_income = txn_type == "收入"
    is_expense = txn_type == "支出"
    is_active = status == "active"
    is_personal_advance = "个人垫付" in tags
    dashboard_eligible = is_active and not is_personal_advance
    dashboard_income = amount if dashboard_eligible and is_income else 0.0
    dashboard_expense = amount if dashboard_eligible and is_expense else 0.0
    dashboard_net = dashboard_income - dashboard_expense
    is_need = bool(result.get("is_need"))
    is_fixed = bool(result.get("is_fixed"))
    result.update(
        {
            "income_amount": amount if is_income else 0.0,
            "expense_amount": amount if is_expense else 0.0,
            "net_amount": amount if is_income else (-amount if is_expense else 0.0),
            "amount_bucket": amount_bucket(amount),
            "tags_text": ", ".join(tags),
            "is_income": 1 if is_income else 0,
            "is_expense": 1 if is_expense else 0,
            "is_active": 1 if is_active else 0,
            "is_personal_advance": 1 if is_personal_advance else 0,
            "dashboard_income_amount": dashboard_income,
            "dashboard_expense_amount": dashboard_expense,
            "dashboard_net_amount": dashboard_net,
            "need_expense_amount": dashboard_expense if is_need else 0.0,
            "want_expense_amount": dashboard_expense if not is_need else 0.0,
            "fixed_expense_amount": dashboard_expense if is_fixed else 0.0,
            "variable_expense_amount": dashboard_expense if not is_fixed else 0.0,
            "data_version": DATA_VERSION,
            "_derived_error": date_error or "",
        }
    )
    return result


def derived_values(transaction):
    enriched = enrich_transaction_fields(transaction)
    return tuple(enriched.get(column) for column in DERIVED_COLUMNS)


def clean_tags(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_items = []
        for item in value:
            if isinstance(item, dict):
                item = item.get("text") or item.get("name") or item.get("value") or ""
            raw_items.append(str(item))
        text = ",".join(raw_items)
    else:
        text = str(value)
    tags = []
    seen = set()
    for item in re.split(r"[,，、;；\n\r\t]+", text):
        tag = item.strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
    return tags


def amount_bucket(amount):
    value = abs(_round_money(amount))
    if value < 20:
        return "0-20"
    if value < 50:
        return "20-50"
    if value < 100:
        return "50-100"
    if value < 500:
        return "100-500"
    return "500+"


def _round_money(value):
    try:
        amount = Decimal(str(value if value not in (None, "") else "0"))
    except (InvalidOperation, ValueError):
        amount = Decimal("0")
    return float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _parse_date(value):
    if isinstance(value, datetime.datetime):
        return value.date(), ""
    if isinstance(value, datetime.date):
        return value, ""
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "nan", "nat"}:
        return None, "日期为空，无法计算日期维度。"
    text = text[:10].replace("/", "-")
    try:
        return datetime.date.fromisoformat(text), ""
    except ValueError:
        return None, f"日期格式无效：{str(value)[:80]}"

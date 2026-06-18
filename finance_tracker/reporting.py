import calendar
import datetime
from collections import defaultdict

import pandas as pd

try:
    from .email_service import generate_report_content
    from .ledger import MONTHLY_BUDGET, load_transactions
    from .tagging import clean_tags
except ImportError:
    from email_service import generate_report_content
    from ledger import MONTHLY_BUDGET, load_transactions
    from tagging import clean_tags


CURRENCY = "CNY"
INCOME_TYPE = "收入"
EXPENSE_TYPE = "支出"
UNKNOWN = "暂无数据"


def build_monthly_bill_payload(month=None):
    target_month = normalize_month(month)
    month_df = _month_df(_active_transactions(), target_month)
    expense_df = _type_df(month_df, EXPENSE_TYPE)
    income_df = _type_df(month_df, INCOME_TYPE)
    today = datetime.date.today()
    today_expense = (
        _sum_amount(expense_df[expense_df["date_only"] == today])
        if target_month == today.strftime("%Y-%m")
        else 0.0
    )

    return {
        "report_type": "monthly_bill",
        "month": target_month,
        "currency": CURRENCY,
        "generated_at": _generated_at(),
        "overview": _monthly_overview(month_df, target_month, today_expense),
        "budget": _budget_payload(_sum_amount(expense_df)),
        "top_categories": _category_summary(expense_df),
        "top_tags": _tag_summary(expense_df),
        "top_expenses": _top_expenses(expense_df),
        "compare_previous_month": _compare_previous_month(target_month),
    }


def build_daily_report_payload(date=None):
    target_date = normalize_date(date or datetime.date.today())
    df = _active_transactions()
    day_df = df[df["date_only"] == target_date] if not df.empty else df
    month_to_date_df = _month_to_date_df(df, target_date)
    expense_df = _type_df(day_df, EXPENSE_TYPE)

    return {
        "report_type": "daily_report",
        "date": target_date.isoformat(),
        "currency": CURRENCY,
        "generated_at": _generated_at(),
        "email_daily_report_markdown": _email_daily_markdown(df, target_date),
        "overview": {
            "income": _money(_sum_type(day_df, INCOME_TYPE)),
            "expense": _money(_sum_type(day_df, EXPENSE_TYPE)),
            "balance": _money(
                _sum_type(day_df, INCOME_TYPE) - _sum_type(day_df, EXPENSE_TYPE)
            ),
            "transaction_count": int(len(day_df)),
            "month_expense_to_date": _money(_sum_type(month_to_date_df, EXPENSE_TYPE)),
            "month_income_to_date": _money(_sum_type(month_to_date_df, INCOME_TYPE)),
        },
        "category_summary": _category_summary(expense_df),
        "tag_summary": _tag_summary(expense_df),
        "transactions": _transaction_rows(day_df, limit=10),
        "budget": _budget_payload(_sum_type(month_to_date_df, EXPENSE_TYPE)),
    }


def build_monthly_tag_analysis_payload(month=None):
    target_month = normalize_month(month)
    month_df = _month_df(_active_transactions(), target_month)
    expense_df = _type_df(month_df, EXPENSE_TYPE)
    tagged_count = _tagged_transaction_count(expense_df)
    total_count = int(len(expense_df))

    return {
        "report_type": "monthly_tag_analysis",
        "month": target_month,
        "currency": CURRENCY,
        "generated_at": _generated_at(),
        "overview": {
            "total_expense": _money(_sum_amount(expense_df)),
            "transaction_count": total_count,
            "tagged_transaction_count": tagged_count,
            "untagged_transaction_count": total_count - tagged_count,
            "tag_coverage_rate": _percent(tagged_count, total_count),
        },
        "tag_summary": _tag_summary(expense_df),
        "tag_groups": _tag_groups(expense_df),
        "compare_previous_month": _compare_previous_month(target_month),
    }


def build_monthly_consumption_report_payload(month=None):
    target_month = normalize_month(month)
    month_df = _month_df(_active_transactions(), target_month)
    expense_df = _type_df(month_df, EXPENSE_TYPE)

    return {
        "report_type": "monthly_consumption_report",
        "month": target_month,
        "currency": CURRENCY,
        "generated_at": _generated_at(),
        "overview": _monthly_overview(month_df, target_month),
        "budget": _budget_payload(
            _sum_amount(expense_df),
            include_time_progress=True,
            month=target_month,
        ),
        "category_summary": _category_summary(expense_df),
        "tag_summary": _tag_summary(expense_df),
        "need_vs_want": _binary_summary(
            expense_df,
            "is_need",
            {1: "刚需", 0: "非刚需"},
        ),
        "fixed_vs_variable": _binary_summary(
            expense_df,
            "is_fixed",
            {1: "固定支出", 0: "变动支出"},
        ),
        "daily_trend": _daily_trend(month_df, target_month),
        "top_expenses": _top_expenses(expense_df),
        "compare_previous_month": _compare_previous_month(target_month),
    }


def generate_daily_report(target_date=None):
    """Return the same Markdown body used by the email daily report."""
    report_date = normalize_date(target_date or datetime.date.today())
    return _email_daily_markdown(_active_transactions(), report_date)


def generate_monthly_report(month=None):
    payload = build_monthly_bill_payload(month)
    overview = payload["overview"]
    lines = [
        f"# 记账月报 {payload['month']}",
        "",
        f"- 收入: ¥{overview['income']:.2f}",
        f"- 支出: ¥{overview['expense']:.2f}",
        f"- 结余: ¥{overview['balance']:.2f}",
        f"- 交易笔数: {overview['transaction_count']}",
        "",
        "## 支出 Top 10",
        *_expense_markdown_lines(payload["top_expenses"]),
    ]
    return "\n".join(lines).strip()


def generate_yearly_report(year=None):
    target_year = normalize_year(year)
    df = _active_transactions()
    if not df.empty:
        year_df = df[df["date"].dt.year == target_year]
    else:
        year_df = df
    income = _sum_type(year_df, INCOME_TYPE)
    expense = _sum_type(year_df, EXPENSE_TYPE)
    return "\n".join(
        [
            f"# 记账年报 {target_year}",
            "",
            f"- 年度收入: ¥{income:.2f}",
            f"- 年度支出: ¥{expense:.2f}",
            f"- 年度结余: ¥{income - expense:.2f}",
            f"- 交易笔数: {len(year_df)}",
        ]
    ).strip()


def normalize_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    text = str(value or "").strip()
    if text in {"今天", "今日"}:
        return datetime.date.today()
    if text in {"昨天", "昨日"}:
        return datetime.date.today() - datetime.timedelta(days=1)
    return datetime.datetime.strptime(text[:10], "%Y-%m-%d").date()


def normalize_month(value=None):
    if value in (None, "", "本月"):
        return datetime.date.today().strftime("%Y-%m")
    text = str(value).strip()
    datetime.datetime.strptime(text[:7], "%Y-%m")
    return text[:7]


def normalize_year(value=None):
    if value in (None, "", "今年"):
        return datetime.date.today().year
    return int(str(value).strip()[:4])


def month_range(month):
    target_month = normalize_month(month)
    year = int(target_month[:4])
    month_number = int(target_month[5:7])
    last_day = calendar.monthrange(year, month_number)[1]
    return (
        datetime.date(year, month_number, 1),
        datetime.date(year, month_number, last_day),
    )


def _active_transactions():
    df = load_transactions()
    if df is None or df.empty:
        return _empty_df()
    df = df.copy()
    if "status" in df.columns:
        df = df[df["status"].fillna("active") == "active"].copy()
    df["amount"] = pd.to_numeric(df.get("amount"), errors="coerce").fillna(0.0)
    df["date"] = pd.to_datetime(df.get("date"), errors="coerce")
    df = df.dropna(subset=["date"]).copy()
    df["date_only"] = df["date"].dt.date
    df["month"] = df["date"].dt.strftime("%Y-%m")
    for column in ["type", "category", "description", "tags"]:
        if column not in df.columns:
            df[column] = ""
        df[column] = df[column].fillna("").astype(str)
    for column in ["is_need", "is_fixed"]:
        if column not in df.columns:
            df[column] = 0
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0).astype(int)
    return df


def _empty_df():
    return pd.DataFrame(
        columns=[
            "date",
            "date_only",
            "month",
            "type",
            "category",
            "amount",
            "description",
            "tags",
            "is_need",
            "is_fixed",
            "status",
        ]
    )


def _month_df(df, month):
    return df[df["month"] == month].copy() if not df.empty else df


def _month_to_date_df(df, target_date):
    if df.empty:
        return df
    month = target_date.strftime("%Y-%m")
    return df[(df["month"] == month) & (df["date_only"] <= target_date)].copy()


def _type_df(df, txn_type):
    return df[df["type"] == txn_type].copy() if not df.empty else df


def _sum_type(df, txn_type):
    return _sum_amount(_type_df(df, txn_type))


def _sum_amount(df):
    if df is None or df.empty:
        return 0.0
    return float(pd.to_numeric(df["amount"], errors="coerce").fillna(0.0).sum())


def _monthly_overview(month_df, month, today_expense=0.0):
    income = _sum_type(month_df, INCOME_TYPE)
    expense = _sum_type(month_df, EXPENSE_TYPE)
    expense_df = _type_df(month_df, EXPENSE_TYPE)
    income_df = _type_df(month_df, INCOME_TYPE)
    return {
        "income": _money(income),
        "expense": _money(expense),
        "balance": _money(income - expense),
        "saving_rate": _percent(income - expense, income),
        "transaction_count": int(len(month_df)),
        "income_count": int(len(income_df)),
        "expense_count": int(len(expense_df)),
        "daily_avg_expense": _money(expense / _days_elapsed_for_month(month)),
        "today_expense": _money(today_expense),
    }


def _budget_payload(used, include_time_progress=False, month=None):
    budget = float(MONTHLY_BUDGET or 0)
    usage_rate = _percent(used, budget)
    payload = {
        "monthly_budget": _money(budget),
        "used": _money(used),
        "remaining": _money(budget - used),
        "usage_rate": usage_rate,
        "status": _budget_status(usage_rate),
    }
    if include_time_progress:
        payload["time_progress_rate"] = _time_progress_rate(month)
    return payload


def _category_summary(expense_df, limit=10):
    if expense_df.empty:
        return []
    total = _sum_amount(expense_df)
    rows = []
    grouped = expense_df.groupby("category", dropna=False)
    for category, group in grouped:
        amount = _sum_amount(group)
        rows.append(
            {
                "category": str(category or UNKNOWN),
                "amount": _money(amount),
                "count": int(len(group)),
                "share": _percent(amount, total),
            }
        )
    return sorted(rows, key=lambda item: item["amount"], reverse=True)[:limit]


def _tag_summary(expense_df, limit=10):
    if expense_df.empty:
        return []
    total = _sum_amount(expense_df)
    grouped = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for _, row in expense_df.iterrows():
        row_tags = clean_tags(row.get("tags"))
        for tag in row_tags:
            grouped[tag]["amount"] += float(row.get("amount") or 0)
            grouped[tag]["count"] += 1
    rows = [
        {
            "tag": tag,
            "amount": _money(value["amount"]),
            "count": int(value["count"]),
            "share": _percent(value["amount"], total),
        }
        for tag, value in grouped.items()
    ]
    return sorted(rows, key=lambda item: item["amount"], reverse=True)[:limit]


def _top_expenses(expense_df, limit=10):
    return _transaction_rows(
        expense_df.sort_values(["amount", "date"], ascending=[False, False])
        if not expense_df.empty
        else expense_df,
        limit=limit,
    )


def _transaction_rows(df, limit=10):
    if df.empty:
        return []
    rows = []
    for _, row in df.head(limit).iterrows():
        rows.append(
            {
                "date": row["date"].date().isoformat(),
                "type": str(row.get("type") or ""),
                "category": str(row.get("category") or UNKNOWN),
                "amount": _money(row.get("amount") or 0),
                "description": _short_text(row.get("description")),
                "tags": clean_tags(row.get("tags"))[:5],
            }
        )
    return rows


def _compare_previous_month(month):
    current_df = _month_df(_active_transactions(), month)
    previous_month = _previous_month(month)
    previous_df = _month_df(_active_transactions(), previous_month)
    current = _period_totals(current_df)
    previous = _period_totals(previous_df)
    return {
        "previous_month": previous_month,
        "income": _compare_value(current["income"], previous["income"]),
        "expense": _compare_value(current["expense"], previous["expense"]),
        "balance": _compare_value(current["balance"], previous["balance"]),
        "transaction_count": {
            "current": current["transaction_count"],
            "previous": previous["transaction_count"],
            "change": current["transaction_count"] - previous["transaction_count"],
        },
    }


def _period_totals(df):
    income = _sum_type(df, INCOME_TYPE)
    expense = _sum_type(df, EXPENSE_TYPE)
    return {
        "income": income,
        "expense": expense,
        "balance": income - expense,
        "transaction_count": int(len(df)),
    }


def _compare_value(current, previous):
    return {
        "current": _money(current),
        "previous": _money(previous),
        "change": _money(current - previous),
        "change_rate": _percent(current - previous, previous),
    }


def _tag_groups(expense_df):
    groups = {
        "刚需": expense_df[expense_df["is_need"] == 1] if not expense_df.empty else expense_df,
        "非刚需": expense_df[expense_df["is_need"] != 1] if not expense_df.empty else expense_df,
        "固定支出": expense_df[expense_df["is_fixed"] == 1] if not expense_df.empty else expense_df,
        "小额高频": expense_df[expense_df["amount"] <= 50] if not expense_df.empty else expense_df,
    }
    return {
        name: {
            "amount": _money(_sum_amount(group)),
            "count": int(len(group)),
            "top_tags": _tag_summary(group, limit=5),
        }
        for name, group in groups.items()
    }


def _binary_summary(expense_df, field, labels):
    total = _sum_amount(expense_df)
    result = []
    for value in [1, 0]:
        group = expense_df[expense_df[field] == value] if not expense_df.empty else expense_df
        amount = _sum_amount(group)
        result.append(
            {
                "type": labels[value],
                "amount": _money(amount),
                "count": int(len(group)),
                "share": _percent(amount, total),
            }
        )
    return result


def _daily_trend(month_df, month):
    year, month_number = [int(part) for part in month.split("-")]
    last_day = calendar.monthrange(year, month_number)[1]
    rows = []
    for day in range(1, last_day + 1):
        target = datetime.date(year, month_number, day)
        day_df = month_df[month_df["date_only"] == target] if not month_df.empty else month_df
        income = _sum_type(day_df, INCOME_TYPE)
        expense = _sum_type(day_df, EXPENSE_TYPE)
        rows.append(
            {
                "date": target.isoformat(),
                "income": _money(income),
                "expense": _money(expense),
                "balance": _money(income - expense),
                "transaction_count": int(len(day_df)),
            }
        )
    return rows


def _tagged_transaction_count(df):
    if df.empty:
        return 0
    return sum(1 for _, row in df.iterrows() if clean_tags(row.get("tags")))


def _email_daily_markdown(df, target_date):
    try:
        return generate_report_content(df.copy(), target_date)
    except Exception:
        return f"# 记账日报\n\n> 报告日期: {target_date.isoformat()}\n\n暂无邮件日报内容。"


def _previous_month(month):
    year, month_number = [int(part) for part in month.split("-")]
    first_day = datetime.date(year, month_number, 1)
    previous_last_day = first_day - datetime.timedelta(days=1)
    return previous_last_day.strftime("%Y-%m")


def _days_elapsed_for_month(month):
    today = datetime.date.today()
    year, month_number = [int(part) for part in month.split("-")]
    last_day = calendar.monthrange(year, month_number)[1]
    if today.year == year and today.month == month_number:
        return max(today.day, 1)
    return last_day


def _time_progress_rate(month):
    year, month_number = [int(part) for part in normalize_month(month).split("-")]
    last_day = calendar.monthrange(year, month_number)[1]
    today = datetime.date.today()
    if today.year == year and today.month == month_number:
        elapsed = today.day
    elif datetime.date(year, month_number, 1) < today.replace(day=1):
        elapsed = last_day
    else:
        elapsed = 0
    return _percent(elapsed, last_day)


def _budget_status(usage_rate):
    if usage_rate >= 100:
        return "已超支"
    if usage_rate >= 80:
        return "接近超支"
    return "正常"


def _expense_markdown_lines(rows):
    if not rows:
        return ["暂无大额支出。"]
    return [
        f"{index}. {row['date']} {row['category']} ¥{row['amount']:.2f} {row['description']}"
        for index, row in enumerate(rows, start=1)
    ]


def _short_text(value, limit=20):
    text = str(value or "").strip()
    if not text:
        return UNKNOWN
    return text if len(text) <= limit else text[:limit]


def _money(value):
    return round(float(value or 0), 2)


def _percent(numerator, denominator):
    denominator = float(denominator or 0)
    if not denominator:
        return 0.0
    return round(float(numerator or 0) / denominator * 100, 2)


def _generated_at():
    return datetime.datetime.now().replace(microsecond=0).isoformat()

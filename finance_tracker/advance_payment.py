PERSONAL_ADVANCE_TAG = "个人垫付"
PERSONAL_ADVANCE_KEYWORDS = ("个人垫付", "垫付", "代付", "替公司", "标书")


def clean_tag_names(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = str(value or "").replace("，", ",").replace("、", ",").split(",")
    result = []
    for item in raw_items:
        tag = str(item or "").strip()
        if tag and tag not in result:
            result.append(tag)
    return result


def has_personal_advance_tag(value):
    return PERSONAL_ADVANCE_TAG in clean_tag_names(value)


def text_mentions_personal_advance(*values):
    text = " ".join(str(value or "") for value in values)
    return any(keyword in text for keyword in PERSONAL_ADVANCE_KEYWORDS)


def personal_advance_mask(df):
    if df is None or getattr(df, "empty", True) or "tags" not in df.columns:
        return df.index == "__never__"
    return df["tags"].fillna("").astype(str).apply(has_personal_advance_tag)


def actual_transactions_df(df):
    if df is None or getattr(df, "empty", True):
        return df
    return df[~personal_advance_mask(df)].copy()


def personal_advance_df(df):
    if df is None or getattr(df, "empty", True):
        return df
    return df[personal_advance_mask(df)].copy()


def personal_advance_balance(df):
    advance = personal_advance_df(df)
    if advance is None or advance.empty:
        return {
            "advance_expense": 0.0,
            "advance_reimbursement": 0.0,
            "advance_balance": 0.0,
        }
    expense = float(advance.loc[advance["type"] == "支出", "amount"].sum())
    reimbursement = float(advance.loc[advance["type"] == "收入", "amount"].sum())
    return {
        "advance_expense": expense,
        "advance_reimbursement": reimbursement,
        "advance_balance": expense - reimbursement,
    }


def personal_advance_summary(df, limit=5, balance_df=None):
    advance = personal_advance_df(df)
    balance_source = personal_advance_df(balance_df) if balance_df is not None else advance
    current_balance = _advance_balance_value(balance_source)
    if advance is None or advance.empty:
        return {
            "advance_expense": 0.0,
            "advance_reimbursement": 0.0,
            "advance_balance": 0.0,
            "current_balance": current_balance,
            "transaction_count": 0,
            "advance_expense_count": 0,
            "advance_reimbursement_count": 0,
            "transactions": [],
        }

    expense_df = advance[advance["type"] == "支出"]
    reimbursement_df = advance[advance["type"] == "收入"]
    expense = float(expense_df["amount"].sum()) if "amount" in expense_df.columns else 0.0
    reimbursement = (
        float(reimbursement_df["amount"].sum())
        if "amount" in reimbursement_df.columns
        else 0.0
    )
    return {
        "advance_expense": expense,
        "advance_reimbursement": reimbursement,
        "advance_balance": expense - reimbursement,
        "current_balance": current_balance,
        "transaction_count": int(len(advance)),
        "advance_expense_count": int(len(expense_df)),
        "advance_reimbursement_count": int(len(reimbursement_df)),
        "transactions": _transaction_rows(advance, limit=limit),
    }


def _advance_balance_value(df):
    if df is None or getattr(df, "empty", True):
        return 0.0
    expense = float(df.loc[df["type"] == "支出", "amount"].sum())
    reimbursement = float(df.loc[df["type"] == "收入", "amount"].sum())
    return expense - reimbursement


def _transaction_rows(df, limit=5):
    if df is None or getattr(df, "empty", True) or not limit:
        return []
    rows = []
    for _, row in df.head(limit).iterrows():
        date_value = row.get("date")
        if hasattr(date_value, "date"):
            date_text = date_value.date().isoformat()
        else:
            date_text = str(date_value or "")[:10]
        rows.append(
            {
                "date": date_text,
                "type": str(row.get("type") or ""),
                "category": str(row.get("category") or ""),
                "amount": float(row.get("amount") or 0),
                "description": str(row.get("description") or ""),
                "tags": clean_tag_names(row.get("tags")),
            }
        )
    return rows

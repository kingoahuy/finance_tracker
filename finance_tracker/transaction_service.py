import datetime

import pandas as pd

try:
    from .email_service import generate_report_content
    from .ledger import (
        MONTHLY_BUDGET,
        add_transaction,
        claim_pending_action,
        connect,
        create_pending_action,
        expire_pending_actions,
        get_pending_action,
        init_db,
        load_transactions,
        normalize_transaction,
        parse_entry_text,
        resolve_pending_action,
    )
except ImportError:
    from email_service import generate_report_content
    from ledger import (
        MONTHLY_BUDGET,
        add_transaction,
        claim_pending_action,
        connect,
        create_pending_action,
        expire_pending_actions,
        get_pending_action,
        init_db,
        load_transactions,
        normalize_transaction,
        parse_entry_text,
        resolve_pending_action,
    )


MUTATING_INTENTS = {
    "create_transactions",
    "delete_last_transaction",
    "delete_transaction_by_id",
    "update_last_transaction",
    "update_transaction_by_id",
}


def create_transactions_from_text(
    text,
    default_date=None,
    source="streamlit",
    source_message_id=None,
    source_user_open_id=None,
    source_chat_id=None,
    auto_sync=True,
):
    records = parse_entry_text(text, default_date)
    return create_transactions(
        records,
        source=source,
        source_message_id=source_message_id,
        source_user_open_id=source_user_open_id,
        source_chat_id=source_chat_id,
        auto_sync=auto_sync,
    )


def create_transactions(
    records,
    source="streamlit",
    source_message_id=None,
    source_user_open_id=None,
    source_chat_id=None,
    auto_sync=True,
):
    return [
        create_transaction(
            record,
            source=source,
            source_message_id=source_message_id,
            source_user_open_id=source_user_open_id,
            source_chat_id=source_chat_id,
            auto_sync=auto_sync,
        )
        for record in records
    ]


def create_transaction(
    data,
    source="streamlit",
    source_message_id=None,
    source_user_open_id=None,
    source_chat_id=None,
    auto_sync=True,
):
    payload = dict(data)
    payload["source"] = source
    payload["source_message_id"] = source_message_id
    payload["source_user_open_id"] = source_user_open_id
    payload["source_chat_id"] = source_chat_id
    saved = add_transaction(payload)
    if auto_sync:
        _try_sync(saved["transaction_uid"])
    return saved


def get_today_summary(target_date=None):
    target_date = _as_date(target_date or datetime.date.today())
    df = _normalized_transactions()
    day_df = df[df["date"].dt.date == target_date] if not df.empty else df
    return {
        "date": target_date.isoformat(),
        "income": _sum_type(day_df, "收入"),
        "expense": _sum_type(day_df, "支出"),
        "balance": _sum_type(day_df, "收入") - _sum_type(day_df, "支出"),
        "count": int(len(day_df)),
        "transactions": _records(day_df),
    }


def get_month_summary(target_date=None):
    target_date = _as_date(target_date or datetime.date.today())
    df = _normalized_transactions()
    if df.empty:
        month_df = df
    else:
        month_df = df[
            (df["date"].dt.year == target_date.year)
            & (df["date"].dt.month == target_date.month)
            & (df["date"].dt.date <= target_date)
        ]
    income = _sum_type(month_df, "收入")
    expense = _sum_type(month_df, "支出")
    expense_df = month_df[month_df["type"] == "支出"] if not month_df.empty else month_df
    grouped = (
        expense_df.groupby("category", dropna=False)["amount"]
        .sum()
        .sort_values(ascending=False)
        .head(3)
        if not expense_df.empty
        else []
    )
    top_categories = (
        [{"category": str(category), "amount": float(amount)} for category, amount in grouped.items()]
        if hasattr(grouped, "items")
        else []
    )
    return {
        "month": target_date.strftime("%Y-%m"),
        "income": income,
        "expense": expense,
        "balance": income - expense,
        "budget": float(MONTHLY_BUDGET),
        "budget_usage": (expense / MONTHLY_BUDGET * 100) if MONTHLY_BUDGET else 0.0,
        "count": int(len(month_df)),
        "top_categories": top_categories,
    }


def get_recent_transactions(limit=5, sender_open_id=None, chat_id=None):
    safe_limit = max(1, min(int(limit), 50))
    df = _normalized_transactions()
    if sender_open_id:
        df = df[
            (df["source"] == "feishu")
            & (df["source_user_open_id"] == sender_open_id)
        ]
    if chat_id:
        df = df[df["source_chat_id"] == chat_id]
    return _records(df.head(safe_limit))


def get_owned_transaction(sender_open_id, chat_id=None, transaction_id=None):
    init_db()
    conditions = [
        "source = 'feishu'",
        "source_user_open_id = ?",
        "status = 'active'",
    ]
    params = [str(sender_open_id or "")]
    if chat_id:
        conditions.append("source_chat_id = ?")
        params.append(str(chat_id))
    if transaction_id is not None:
        conditions.append("id = ?")
        params.append(int(transaction_id))
    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT rowid, id, transaction_uid, date, type, category, amount,
                   description, tags, is_need, is_fixed, source, source_message_id,
                   source_user_open_id, source_chat_id, status
            FROM transactions
            WHERE {' AND '.join(conditions)}
            ORDER BY rowid DESC LIMIT 1
            """,
            params,
        ).fetchone()
    if not row:
        return None
    keys = [
        "_rowid", "id", "transaction_uid", "date", "type", "category", "amount",
        "description", "tags", "is_need", "is_fixed", "source", "source_message_id",
        "source_user_open_id", "source_chat_id", "status",
    ]
    return dict(zip(keys, row))


def soft_delete_transaction(
    sender_open_id,
    chat_id=None,
    transaction_id=None,
    reason="feishu user request",
    auto_sync=True,
):
    record = get_owned_transaction(sender_open_id, chat_id, transaction_id)
    if not record:
        return None
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE transactions
            SET status = 'deleted', deleted_at = CURRENT_TIMESTAMP,
                deleted_by_open_id = ?, delete_reason = ?,
                updated_at = CURRENT_TIMESTAMP, sync_status = 'pending',
                sync_error = ''
            WHERE rowid = ? AND status = 'active'
            """,
            (str(sender_open_id), str(reason)[:200], record["_rowid"]),
        )
        if cursor.rowcount != 1:
            return None
        _enqueue_update(conn, record["transaction_uid"])
    record["status"] = "deleted"
    if auto_sync:
        _try_sync(record["transaction_uid"])
    return record


def update_owned_transaction(
    sender_open_id,
    updates,
    chat_id=None,
    transaction_id=None,
    auto_sync=True,
):
    record = get_owned_transaction(sender_open_id, chat_id, transaction_id)
    if not record:
        return None
    merged = {**record, **dict(updates or {})}
    normalized = normalize_transaction(merged)
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE transactions
            SET date = ?, type = ?, category = ?, amount = ?, description = ?,
                tags = ?, is_need = ?, is_fixed = ?, updated_at = CURRENT_TIMESTAMP,
                sync_status = 'pending', sync_error = ''
            WHERE rowid = ? AND status = 'active'
              AND source = 'feishu' AND source_user_open_id = ?
            """,
            (
                normalized["date"], normalized["type"], normalized["category"],
                normalized["amount"], normalized["description"], normalized["tags"],
                normalized["is_need"], normalized["is_fixed"], record["_rowid"],
                str(sender_open_id),
            ),
        )
        if cursor.rowcount != 1:
            return None
        _enqueue_update(conn, record["transaction_uid"])
    updated = {**record, **normalized}
    if auto_sync:
        _try_sync(record["transaction_uid"])
    return updated


def queue_action(action, sender_open_id, chat_id, source_message_id=None):
    intent = action.get("intent")
    if intent not in MUTATING_INTENTS:
        raise ValueError("Only mutating actions can be queued.")
    return create_pending_action(
        intent,
        action,
        sender_open_id,
        chat_id,
        source_message_id=source_message_id,
        ttl_minutes=10,
    )


def resolve_action(action_id, operation, sender_open_id, chat_id):
    expire_pending_actions()
    action = get_pending_action(action_id)
    if not action:
        return {"success": False, "status": "missing", "message": "确认操作不存在。"}
    if action["sender_open_id"] != str(sender_open_id) or action["chat_id"] != str(chat_id):
        return {"success": False, "status": "forbidden", "message": "你无权处理这个操作。"}
    if action["status"] != "pending":
        return {
            "success": action["status"] == "confirmed",
            "status": action["status"],
            "message": _resolved_message(action["status"]),
            "result": action.get("result"),
        }
    if operation == "cancel":
        resolve_pending_action(action_id, "cancelled", {"cancelled": True})
        return {"success": True, "status": "cancelled", "message": "已取消。"}
    if operation != "confirm":
        return {"success": False, "status": "invalid", "message": "无效的卡片操作。"}
    if not claim_pending_action(action_id):
        refreshed = get_pending_action(action_id)
        return {
            "success": False,
            "status": refreshed["status"] if refreshed else "missing",
            "message": "该操作已被处理或已过期。",
        }
    try:
        result = execute_mutating_action(
            action["intent"],
            action["payload"],
            sender_open_id,
            chat_id,
            action.get("source_message_id"),
        )
        status = "confirmed" if result.get("success") else "failed"
        resolve_pending_action(action_id, status, result)
        return {"status": status, **result}
    except Exception as exc:
        failure = {"success": False, "message": "执行失败，请稍后重试。", "error": type(exc).__name__}
        resolve_pending_action(action_id, "failed", failure)
        return {"status": "failed", **failure}


def execute_mutating_action(intent, payload, sender_open_id, chat_id, source_message_id=None):
    if intent == "create_transactions":
        records = create_transactions(
            payload.get("transactions") or [],
            source="feishu",
            source_message_id=source_message_id,
            source_user_open_id=sender_open_id,
            source_chat_id=chat_id,
        )
        return {"success": bool(records), "transactions": records, "message": f"已记录 {len(records)} 笔流水。"}
    transaction_id = payload.get("transaction_id")
    if intent in {"delete_last_transaction", "delete_transaction_by_id"}:
        record = soft_delete_transaction(
            sender_open_id,
            chat_id,
            transaction_id if intent.endswith("by_id") else None,
        )
        return {
            "success": bool(record),
            "record": record,
            "message": "流水已删除。" if record else "未找到你可删除的有效飞书流水。",
        }
    if intent in {"update_last_transaction", "update_transaction_by_id"}:
        record = update_owned_transaction(
            sender_open_id,
            payload.get("updates") or {},
            chat_id,
            transaction_id if intent.endswith("by_id") else None,
        )
        return {
            "success": bool(record),
            "record": record,
            "message": "流水已修改。" if record else "未找到你可修改的有效飞书流水。",
        }
    return {"success": False, "message": "不支持的确认操作。"}


def undo_last_transaction(source=None, source_message_id=None):
    # Compatibility wrapper. New Feishu paths should call soft_delete_transaction
    # with the sender identity and confirmation.
    if source != "feishu":
        return None
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT source_user_open_id, source_chat_id
            FROM transactions
            WHERE source = 'feishu' AND status = 'active'
              AND (? IS NULL OR source_message_id = ?)
            ORDER BY rowid DESC LIMIT 1
            """,
            (source_message_id, source_message_id),
        ).fetchone()
    if not row or not row[0]:
        return None
    return soft_delete_transaction(row[0], row[1], auto_sync=True)


def generate_daily_report(target_date=None):
    target_date = _as_date(target_date or datetime.date.today())
    return generate_report_content(load_transactions(), target_date)


def _enqueue_update(conn, transaction_uid):
    conn.execute(
        """
        INSERT INTO sync_outbox
            (transaction_uid, operation, status, retry_count, updated_at)
        VALUES (?, 'update', 'pending', 0, CURRENT_TIMESTAMP)
        """,
        (transaction_uid,),
    )


def _try_sync(transaction_uid):
    try:
        from .bitable_sync import auto_sync_enabled, sync_transaction
    except ImportError:
        try:
            from bitable_sync import auto_sync_enabled, sync_transaction
        except ImportError:
            return
    if not auto_sync_enabled():
        return
    try:
        sync_transaction(transaction_uid)
    except Exception:
        return


def _normalized_transactions():
    df = load_transactions()
    if df.empty:
        return df
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    return df.dropna(subset=["date"])


def _sum_type(df, transaction_type):
    if df is None or df.empty:
        return 0.0
    return float(df.loc[df["type"] == transaction_type, "amount"].sum())


def _records(df):
    if df is None or df.empty:
        return []
    result = df.drop(columns=["_rowid"], errors="ignore").copy()
    result["date"] = result["date"].dt.strftime("%Y-%m-%d")
    result = result.where(pd.notna(result), None)
    return result.to_dict(orient="records")


def _resolved_message(status):
    return {
        "confirmed": "该操作已经确认过。",
        "cancelled": "该操作已经取消。",
        "expired": "该操作已过期，请重新发起。",
        "failed": "该操作此前执行失败。",
        "processing": "该操作正在处理中。",
    }.get(status, "该操作已处理。")


def _as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.datetime.strptime(str(value), "%Y-%m-%d").date()

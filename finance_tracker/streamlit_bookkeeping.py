"""DeepSeek-only bookkeeping helpers used by the Streamlit entry page."""

try:
    from .ai_parser import parse_action
    from .transaction_service import create_transactions
except ImportError:
    from ai_parser import parse_action
    from transaction_service import create_transactions


def parse_web_bookkeeping(text, default_date=None, parser=None):
    """Parse a web bookkeeping request through DeepSeek without local fallback."""
    action = (parser or parse_action)(
        text,
        default_date=default_date,
        context={"task_mode": "bookkeeping_only"},
        ai_only=True,
    )
    intent = str(action.get("intent") or "unknown")
    if intent == "create_transactions" and action.get("transactions"):
        if action.get("parser") != "ai":
            return {
                "success": False,
                "action": action,
                "message": "网页记账只接受 DeepSeek 解析结果，请稍后重试。",
            }
        return {
            "success": True,
            "action": action,
            "message": "DeepSeek 已生成记账草稿，请确认后写入。",
        }
    if intent == "ask_clarification":
        return {
            "success": False,
            "action": action,
            "needs_clarification": True,
            "message": action.get("clarification_question") or "请补充金额、日期或收支事项。",
        }
    if intent == "chat" and action.get("reply"):
        return {"success": False, "action": action, "message": action["reply"]}
    reason = str(action.get("reason") or "")
    if reason == "deepseek_disabled_or_unconfigured":
        message = "DeepSeek 未启用或未配置 API Key，网页端不会退回本地识别。"
    elif reason == "ai_timeout":
        message = "DeepSeek Pro 本次响应超时，没有写入账本；请直接重试一次。"
    elif reason == "invalid_json":
        message = "DeepSeek 本次返回格式异常，没有写入账本；请直接重试一次。"
    elif reason == "low_confidence":
        message = "DeepSeek 对这条内容把握不足，请补充明确的金额和事项后重试。"
    elif reason in {"recurrence_count_mismatch", "recurrence_not_expanded"}:
        message = "DeepSeek 未完整展开周期流水，请把周期、频率和每次金额写明确后重试。"
    elif reason == "multiple_transactions_incomplete":
        message = "DeepSeek 遗漏了部分事项，请用逗号或换行分开每笔收支后重试。"
    elif reason == "future_transaction_rejected":
        message = "草稿包含尚未发生的未来流水，系统已拒绝写入；请明确已发生的日期范围。"
    elif reason == "unnecessary_clarification":
        message = "DeepSeek 仍未正确使用已提供的金额，请换一种简短说法后重试。"
    elif reason in {"ai_client_error", "ai_error"}:
        message = "DeepSeek 接口本次调用异常，没有写入账本；请稍后重试。"
    else:
        message = "DeepSeek 暂未生成可校验的记账草稿，没有写入账本；请重试或补充信息。"
    return {"success": False, "action": action, "message": message}


def preview_web_action(action):
    return [
        {
            "日期": row.get("date"),
            "类型": row.get("type"),
            "分类": row.get("category"),
            "金额": float(row.get("amount") or 0),
            "描述": row.get("description"),
            "标签": row.get("tags") or "",
        }
        for row in (action or {}).get("transactions", [])
    ]


def commit_web_bookkeeping(action, writer=None):
    """Write a confirmed DeepSeek action to SQLite and queue Feishu sync."""
    if not isinstance(action, dict) or action.get("intent") != "create_transactions":
        raise ValueError("没有可确认的记账草稿。")
    if action.get("parser") != "ai":
        raise ValueError("网页端只允许写入 DeepSeek 解析的记账草稿。")
    records = list(action.get("transactions") or [])
    if not records:
        raise ValueError("记账草稿中没有有效流水。")
    create = writer or create_transactions
    return create(records, source="streamlit_deepseek", auto_sync=True)

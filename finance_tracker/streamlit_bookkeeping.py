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
        context={},
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
    else:
        message = "DeepSeek 暂未生成可靠的记账草稿，请补充信息后重试。"
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

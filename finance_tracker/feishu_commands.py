import re

try:
    from .ai_parser import parse_action
    from .transaction_service import (
        generate_daily_report,
        get_month_summary,
        get_recent_transactions,
        get_today_summary,
        queue_action,
    )
except ImportError:
    from ai_parser import parse_action
    from transaction_service import (
        generate_daily_report,
        get_month_summary,
        get_recent_transactions,
        get_today_summary,
        queue_action,
    )


HELP_TEXT = """智账 Pro 飞书记账

可用命令：
- 今日账单
- 本月账单
- 最近5笔 / 最近N笔
- 生成日报
- 同步看板
- 撤销上一笔
- 删除 ID 12

也可以直接发送自然语言：
午饭25
昨天打车36.5，晚饭42
把上一笔金额改成30"""


def route_command(text, context=None, sync_callback=None, parser=None):
    context = context or {}
    clean_text = str(text or "").strip()
    if not clean_text:
        return {"success": False, "text": _parse_failure_text(), "action": "invalid"}

    explicit = _route_explicit(clean_text, context, sync_callback)
    if explicit:
        return explicit

    action = (parser or parse_action)(clean_text)
    return _route_parsed_action(action, context, sync_callback)


def _route_explicit(text, context, sync_callback):
    if text in {"帮助", "help", "/help"}:
        return {"success": True, "text": HELP_TEXT, "action": "help"}
    if text == "今日账单":
        return {"success": True, "text": _format_today(get_today_summary()), "action": "today"}
    if text == "本月账单":
        return {"success": True, "text": _format_month(get_month_summary()), "action": "month"}

    recent_match = re.fullmatch(r"最近\s*(\d+)\s*笔", text)
    if recent_match:
        limit = max(1, min(int(recent_match.group(1)), 20))
        records = get_recent_transactions(
            limit,
            sender_open_id=context.get("sender_open_id"),
            chat_id=context.get("chat_id"),
        )
        return {
            "success": True,
            "text": _format_recent(records, limit),
            "card": recent_transactions_card(records, limit),
            "action": "recent",
        }
    if text == "生成日报":
        return {
            "success": True,
            "text": generate_daily_report(),
            "action": "report",
            "format": "markdown",
        }
    if text == "同步看板":
        return _run_sync(sync_callback)
    if text == "撤销上一笔":
        return _queue_confirmation(
            {
                "intent": "delete_last_transaction",
                "confidence": 1.0,
                "transactions": [],
                "transaction_id": None,
                "updates": {},
                "need_confirmation": True,
            },
            context,
        )
    delete_match = re.fullmatch(r"(?:删除|撤销)\s*(?:ID|id|第)?\s*(\d+)\s*(?:笔)?", text)
    if delete_match:
        return _queue_confirmation(
            {
                "intent": "delete_transaction_by_id",
                "confidence": 1.0,
                "transactions": [],
                "transaction_id": int(delete_match.group(1)),
                "updates": {},
                "need_confirmation": True,
            },
            context,
        )
    return None


def _route_parsed_action(action, context, sync_callback):
    intent = action.get("intent")
    if intent in {
        "create_transactions",
        "delete_last_transaction",
        "delete_transaction_by_id",
        "update_last_transaction",
        "update_transaction_by_id",
    }:
        return _queue_confirmation(action, context)
    if intent == "query_today_summary":
        return {"success": True, "text": _format_today(get_today_summary()), "action": "today"}
    if intent == "query_month_summary":
        return {"success": True, "text": _format_month(get_month_summary()), "action": "month"}
    if intent == "query_recent_transactions":
        limit = action.get("limit", 5)
        records = get_recent_transactions(
            limit,
            sender_open_id=context.get("sender_open_id"),
            chat_id=context.get("chat_id"),
        )
        return {
            "success": True,
            "text": _format_recent(records, limit),
            "card": recent_transactions_card(records, limit),
            "action": "recent",
        }
    if intent == "generate_report":
        return {"success": True, "text": generate_daily_report(), "action": "report"}
    if intent == "sync_bitable":
        return _run_sync(sync_callback)
    if intent == "help":
        return {"success": True, "text": HELP_TEXT, "action": "help"}
    return {"success": False, "text": _parse_failure_text(), "action": "invalid"}


def _queue_confirmation(action, context):
    sender_open_id = context.get("sender_open_id")
    chat_id = context.get("chat_id")
    if not sender_open_id or not chat_id:
        return {
            "success": False,
            "text": "当前消息缺少用户或会话身份，不能创建待确认操作。",
            "action": "invalid",
        }
    pending = queue_action(
        action,
        sender_open_id,
        chat_id,
        source_message_id=context.get("message_id"),
    )
    return {
        "success": True,
        "text": "请在卡片中确认本次操作。",
        "card": confirmation_card(pending),
        "action": action["intent"],
        "pending_action_id": pending["action_id"],
    }


def confirmation_card(pending):
    payload = pending["payload"]
    intent = pending["intent"]
    title = {
        "create_transactions": "确认记账",
        "delete_last_transaction": "确认删除上一笔",
        "delete_transaction_by_id": "确认删除指定流水",
        "update_last_transaction": "确认修改上一笔",
        "update_transaction_by_id": "确认修改指定流水",
    }.get(intent, "确认操作")
    lines = _action_preview(payload)
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
        "body": {
            "elements": [
                {"tag": "markdown", "content": lines},
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "确认"},
                            "type": "primary",
                            "value": {
                                "action_id": pending["action_id"],
                                "operation": "confirm",
                            },
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "取消"},
                            "type": "default",
                            "value": {
                                "action_id": pending["action_id"],
                                "operation": "cancel",
                            },
                        },
                    ],
                },
                {"tag": "note", "elements": [{"tag": "plain_text", "content": "10 分钟内有效"}]},
            ]
        },
    }


def recent_transactions_card(records, limit):
    elements = []
    if not records:
        elements.append({"tag": "markdown", "content": "暂无流水。"})
    for index, row in enumerate(records, 1):
        elements.append(
            {
                "tag": "markdown",
                "content": (
                    f"**{index}. {row['date']} {row['type']} ¥{float(row['amount']):.2f}**\n"
                    f"{row['category']}｜{row['description']}｜ID {row.get('id', '-')}"
                ),
            }
        )
    return {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": f"最近 {min(limit, len(records))} 笔"},
            "template": "turquoise",
        },
        "body": {"elements": elements},
    }


def result_card(result):
    success = bool(result.get("success"))
    return {
        "schema": "2.0",
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "操作完成" if success else "操作未完成",
            },
            "template": "green" if success else "orange",
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": str(result.get("message") or "已处理。")}
            ]
        },
    }


def _action_preview(payload):
    intent = payload.get("intent")
    if intent == "create_transactions":
        rows = payload.get("transactions") or []
        lines = [
            f"{index}. {row['date']} {row['type']} **¥{float(row['amount']):.2f}**｜{row['category']}｜{row['description']}"
            for index, row in enumerate(rows, 1)
        ]
        return "\n".join(lines) or "未识别到流水。"
    transaction_id = payload.get("transaction_id")
    target = f"ID {transaction_id}" if transaction_id else "你在当前会话的上一笔有效流水"
    if intent.startswith("delete_"):
        return f"将软删除：**{target}**"
    updates = payload.get("updates") or {}
    details = "\n".join(f"- {key}: {value}" for key, value in updates.items())
    return f"将修改：**{target}**\n{details or '- 未提供有效修改字段'}"


def _run_sync(sync_callback):
    if sync_callback is None:
        return {"success": False, "text": "多维表格同步当前不可用。", "action": "sync"}
    result = sync_callback()
    return {
        "success": bool(result.get("success")),
        "text": result.get("message", "同步任务已完成。"),
        "action": "sync",
    }


def _format_today(summary):
    return "\n".join(
        [
            f"今日账单｜{summary['date']}",
            "",
            f"收入：¥{summary['income']:.2f}",
            f"支出：¥{summary['expense']:.2f}",
            f"结余：¥{summary['balance']:.2f}",
            f"共 {summary['count']} 笔",
        ]
    )


def _format_month(summary):
    lines = [
        f"本月账单｜{summary['month']}",
        "",
        f"本月收入：¥{summary['income']:.2f}",
        f"本月支出：¥{summary['expense']:.2f}",
        f"本月结余：¥{summary['balance']:.2f}",
        f"预算使用率：{summary['budget_usage']:.1f}%",
        "",
        "支出前三分类：",
    ]
    lines.extend(
        (
            f"{index}. {item['category']} ¥{item['amount']:.2f}"
            for index, item in enumerate(summary["top_categories"], 1)
        )
        if summary["top_categories"]
        else ["暂无支出"]
    )
    return "\n".join(lines)


def _format_recent(records, limit):
    if not records:
        return "暂无流水。"
    lines = [f"最近 {min(limit, len(records))} 笔", ""]
    lines.extend(
        f"{index}. {row['date']} {row['type']} ¥{float(row['amount']):.2f}｜{row['category']}｜{row['description']}｜ID {row.get('id', '-')}"
        for index, row in enumerate(records, 1)
    )
    return "\n".join(lines)


def _parse_failure_text():
    return """没有可靠识别出你的操作。
示例：
午饭25
昨天打车36.5，晚饭42
今日账单
撤销上一笔"""

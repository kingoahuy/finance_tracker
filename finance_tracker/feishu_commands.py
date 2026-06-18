import datetime
import re

try:
    from .ai_parser import parse_action
    from .analytics import (
        generate_finance_insights,
        get_budget_warning,
        get_category_expense_summary,
        get_finance_overview,
        get_tag_summary,
    )
    from .deepseek_reports import (
        call_deepseek_report,
        fallback_daily_report_markdown,
        fallback_monthly_bill_markdown,
        fallback_monthly_consumption_report_markdown,
        fallback_monthly_tag_analysis_markdown,
    )
    from .ledger import get_feishu_session, save_feishu_session
    from .reporting import (
        build_daily_report_payload,
        build_monthly_bill_payload,
        build_monthly_consumption_report_payload,
        build_monthly_tag_analysis_payload,
        generate_daily_report,
        generate_monthly_report,
        generate_yearly_report,
    )
    from .transaction_service import (
        get_category_summary,
        get_recent_pending_action,
        get_month_summary,
        get_recent_transactions,
        get_today_summary,
        prepare_action,
        queue_action,
        resolve_action,
        revise_pending_action,
    )
except ImportError:
    from ai_parser import parse_action
    from analytics import (
        generate_finance_insights,
        get_budget_warning,
        get_category_expense_summary,
        get_finance_overview,
        get_tag_summary,
    )
    from deepseek_reports import (
        call_deepseek_report,
        fallback_daily_report_markdown,
        fallback_monthly_bill_markdown,
        fallback_monthly_consumption_report_markdown,
        fallback_monthly_tag_analysis_markdown,
    )
    from ledger import get_feishu_session, save_feishu_session
    from reporting import (
        build_daily_report_payload,
        build_monthly_bill_payload,
        build_monthly_consumption_report_payload,
        build_monthly_tag_analysis_payload,
        generate_daily_report,
        generate_monthly_report,
        generate_yearly_report,
    )
    from transaction_service import (
        get_category_summary,
        get_recent_pending_action,
        get_month_summary,
        get_recent_transactions,
        get_today_summary,
        prepare_action,
        queue_action,
        resolve_action,
        revise_pending_action,
    )


PROMPT_GROUPS = (
    (
        "快速记账",
        (
            "午饭 28",
            "今天下午在食堂吃饭花了10.4元",
            "昨天打车 36.5",
            "收到工资 20000",
            "报销到账 300",
        ),
    ),
    (
        "修改与删除",
        (
            "删除上一笔",
            "撤销上一笔",
            "修改上一笔金额为 32",
            "上一笔分类改成交通",
            "上一笔描述改成和朋友吃饭",
            "删除 ID 12",
        ),
    ),
    (
        "查账",
        (
            "今日账单",
            "本月账单",
            "本月账单简版",
            "最近5笔",
            "最近10笔",
            "餐饮这个月花了多少",
            "今天花了多少钱",
            "这个月收入多少",
            "这个月支出多少",
        ),
    ),
    (
        "报告",
        (
            "记账日报",
            "生成今日日报",
            "生成昨天日报",
            "生成 2026-06-14 日报",
            "本月标签分析",
            "本月消费报告",
            "生成本月月报",
            "生成 2026-06 月报",
            "生成今年年报",
            "生成 2026 年报",
        ),
    ),
    (
        "同步",
        (
            "同步数据",
            "同步状态",
            "检查同步",
            "同步到飞书多维表格",
        ),
    ),
)


HELP_TEXT = "\n\n".join(
    [f"智账 Pro 飞书记账提示词库"]
    + [
        f"【{title}】\n" + "\n".join(f"- {example}" for example in examples)
        for title, examples in PROMPT_GROUPS
    ]
)


def route_command(
    text,
    context=None,
    sync_callback=None,
    sync_dashboard_callback=None,
    parser=None,
):
    context = context or {}
    clean_text = str(text or "").strip()
    if not clean_text:
        return {"success": False, "text": _parse_failure_text(), "action": "invalid"}
    session = _load_session(context)

    session_result = _handle_session_control(
        clean_text,
        context,
        session,
    )
    if session_result:
        return session_result

    explicit = _route_explicit(
        clean_text,
        context,
        sync_callback,
        sync_dashboard_callback,
    )
    if explicit:
        return explicit

    action = _call_parser(
        parser or parse_action,
        clean_text,
        _parser_context(session),
    )
    return _route_parsed_action(
        action,
        context,
        sync_callback,
        sync_dashboard_callback,
        session,
    )


def prompt_library_card():
    elements = []
    for title, examples in PROMPT_GROUPS:
        elements.append(
            {
                "tag": "markdown",
                "content": (
                    f"**{title}**\n"
                    + "\n".join(f"- `{example}`" for example in examples)
                ),
            }
        )
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": False},
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": "智账 Pro 提示词库",
            },
        },
        "body": {"elements": elements},
    }


def _route_explicit(
    text,
    context,
    sync_callback,
    sync_dashboard_callback,
):
    if text in {"帮助", "你能做什么", "提示词", "记账帮助", "使用说明", "help", "/help"}:
        return {
            "success": True,
            "text": HELP_TEXT,
            "card": prompt_library_card(),
            "action": "help",
        }
    if (
        any(phrase in text for phrase in ("同步财务看板", "更新财务看板", "飞书财务看板同步"))
        or (any(phrase in text for phrase in ("财务看板", "汇总表")) and "同步" in text)
    ):
        return {
            "success": False,
            "text": "财务看板/汇总表同步已停用。现在只支持“同步数据”到原始明细数据表，或发送“同步状态”查看状态。",
            "action": "invalid",
        }
    report_result = _route_report_command(text)
    if report_result:
        return report_result
    if text == "今日账单":
        return {"success": True, "text": _format_today(get_today_summary()), "action": "today"}
    if text == "本月账单简版":
        return {"success": True, "text": _format_month(get_month_summary()), "action": "month"}
    if text in {"今天花了多少钱", "今日花了多少钱"}:
        return {"success": True, "text": _format_today(get_today_summary()), "action": "today"}
    if text in {"这个月支出了多少", "这个月支出多少", "本月支出多少", "本月收入多少"}:
        return {"success": True, "text": _format_month(get_month_summary()), "action": "month"}
    if text in {
        "本月财务分析",
        "看看我的消费结构",
    }:
        return _run_finance_analysis("overview")
    if text == "这个月哪些地方花得最多":
        return _run_finance_analysis("category")
    if text == "本月预算情况":
        return _run_finance_analysis("budget")
    if text == "本月标签分析简版":
        return _run_finance_analysis("tag")
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
    category_query = re.fullmatch(
        r"([\u4e00-\u9fff]{2,4})(?:这个月|本月)?(?:花了多少|支出多少|消费多少)",
        text,
    )
    if category_query:
        summary = get_category_summary(category_query.group(1))
        return {
            "success": True,
            "text": _format_category(summary),
            "action": "category",
        }
    if text in {"同步状态", "检查同步"}:
        return _run_sync_status(sync_dashboard_callback)
    if text in {"同步数据", "同步到飞书多维表格"}:
        return _run_sync(sync_callback, sync_dashboard_callback)
    if text in {"撤销上一笔", "删除上一笔"}:
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


def _route_report_command(text):
    compact = re.sub(r"\s+", "", text)
    if compact in {
        "本月账单",
        "这个月账单",
        "本月收支",
        "这个月花了多少",
        "这个月收入多少",
    }:
        return _run_deepseek_report(
            "monthly_bill_report",
            "monthly_bill",
            build_monthly_bill_payload,
            fallback_monthly_bill_markdown,
        )

    daily_date = _parse_daily_report_date(compact)
    if daily_date is not None:
        return _run_deepseek_report(
            "daily_report",
            "daily_report",
            lambda: build_daily_report_payload(daily_date),
            fallback_daily_report_markdown,
        )

    if compact in {
        "本月标签分析",
        "这个月标签分析",
        "看看我的消费标签",
        "本月消费场景",
        "这个月钱花在哪些场景",
    }:
        return _run_deepseek_report(
            "monthly_tag_analysis",
            "monthly_tag_analysis",
            build_monthly_tag_analysis_payload,
            fallback_monthly_tag_analysis_markdown,
        )

    if compact in {
        "本月消费报告",
        "生成本月消费报告",
        "本月财务分析",
        "看看这个月消费情况",
        "这个月消费怎么样",
    }:
        return _run_deepseek_report(
            "monthly_consumption_report",
            "monthly_consumption_report",
            build_monthly_consumption_report_payload,
            fallback_monthly_consumption_report_markdown,
        )

    daily_match = re.fullmatch(
        r"生成(?:(今天|今日|昨天|昨日)|(\d{4}-\d{1,2}-\d{1,2}))?日报",
        compact,
    )
    if daily_match:
        if daily_match.group(2):
            target = daily_match.group(2)
        elif daily_match.group(1) in {"昨天", "昨日"}:
            target = datetime.date.today() - datetime.timedelta(days=1)
        else:
            target = datetime.date.today()
        return _run_deepseek_report(
            "daily_report",
            "daily_report",
            lambda: build_daily_report_payload(target),
            fallback_daily_report_markdown,
        )

    monthly_match = re.fullmatch(
        r"生成(?:(本月)|(\d{4}-\d{1,2}))月报",
        compact,
    )
    if monthly_match:
        target_month = monthly_match.group(2) or None
        return {
            "success": True,
            "text": generate_monthly_report(target_month),
            "action": "monthly_report",
        }

    yearly_match = re.fullmatch(
        r"生成(?:(今年)|(\d{4}))年报",
        compact,
    )
    if yearly_match:
        target_year = yearly_match.group(2) or None
        return {
            "success": True,
            "text": generate_yearly_report(target_year),
            "action": "yearly_report",
        }
    return None


def _run_deepseek_report(action_name, prompt_name, payload_builder, fallback_builder):
    data_payload = payload_builder()
    try:
        markdown = call_deepseek_report(prompt_name, data_payload)
    except Exception:
        markdown = fallback_builder(data_payload)
    return {
        "success": True,
        "text": markdown,
        "action": action_name,
    }


def _parse_daily_report_date(compact):
    if compact in {"记账日报", "生成今日日报", "生成今天日报", "生成日报"}:
        return datetime.date.today()
    if compact in {"生成昨天日报", "生成昨日日报"}:
        return datetime.date.today() - datetime.timedelta(days=1)
    hyphen_date = re.fullmatch(r"(?:生成)?(\d{4}-\d{1,2}-\d{1,2})(?:的)?日报", compact)
    if hyphen_date:
        return hyphen_date.group(1)
    chinese_date = re.fullmatch(
        r"(?:生成)?(\d{4})年(\d{1,2})月(\d{1,2})日(?:的)?日报",
        compact,
    )
    if chinese_date:
        year, month, day = [int(value) for value in chinese_date.groups()]
        return datetime.date(year, month, day)
    return None


def _route_parsed_action(
    action,
    context,
    sync_callback,
    sync_dashboard_callback,
    session=None,
):
    intent = action.get("intent")
    if intent in {
        "create_transactions",
        "delete_last_transaction",
        "delete_transaction_by_id",
        "update_last_transaction",
        "update_transaction_by_id",
    }:
        return _queue_confirmation(action, context)
    if intent == "ask_clarification":
        return _ask_clarification(action, context, session)
    if intent == "revise_pending_action":
        return _revise_pending(action, context, session)
    if intent == "confirm_pending_action":
        return _resolve_pending_from_text(
            "confirm", context, session
        )
    if intent == "cancel_pending_action":
        return _resolve_pending_from_text(
            "cancel", context, session
        )
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
    if intent == "query_category_summary":
        category = (action.get("query") or {}).get("category") or "其他"
        summary = get_category_summary(category)
        return {
            "success": True,
            "text": _format_category(summary),
            "action": "category",
        }
    if intent == "query_finance_analysis":
        return _run_finance_analysis("overview")
    if intent == "query_category_rank":
        return _run_finance_analysis("category")
    if intent == "query_budget_analysis":
        return _run_finance_analysis("budget")
    if intent == "query_tag_analysis":
        return _run_finance_analysis("tag")
    if intent == "monthly_bill_report":
        return _run_deepseek_report(
            "monthly_bill_report",
            "monthly_bill",
            build_monthly_bill_payload,
            fallback_monthly_bill_markdown,
        )
    if intent in {"daily_report", "generate_report", "generate_daily_report"}:
        target = (action.get("query") or {}).get("date") or None
        return _run_deepseek_report(
            "daily_report",
            "daily_report",
            lambda: build_daily_report_payload(target),
            fallback_daily_report_markdown,
        )
    if intent == "monthly_tag_analysis":
        return _run_deepseek_report(
            "monthly_tag_analysis",
            "monthly_tag_analysis",
            build_monthly_tag_analysis_payload,
            fallback_monthly_tag_analysis_markdown,
        )
    if intent == "monthly_consumption_report":
        return _run_deepseek_report(
            "monthly_consumption_report",
            "monthly_consumption_report",
            build_monthly_consumption_report_payload,
            fallback_monthly_consumption_report_markdown,
        )
    if intent == "generate_monthly_report":
        target_month = (action.get("query") or {}).get("month") or None
        return {
            "success": True,
            "text": generate_monthly_report(target_month),
            "action": "monthly_report",
        }
    if intent == "generate_yearly_report":
        target_year = (action.get("query") or {}).get("year") or None
        return {
            "success": True,
            "text": generate_yearly_report(target_year),
            "action": "yearly_report",
        }
    if intent == "sync_bitable":
        return _run_sync(sync_callback, sync_dashboard_callback)
    if intent == "sync_status":
        return _run_sync_status(sync_dashboard_callback)
    if intent == "help":
        return {
            "success": True,
            "text": HELP_TEXT,
            "card": prompt_library_card(),
            "action": "help",
        }
    if intent == "chat":
        return {
            "success": True,
            "text": action.get("reply") or "我在。你可以直接告诉我一笔收支或询问消费情况。",
            "action": "chat",
        }
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
    prepared = prepare_action(action, sender_open_id, chat_id)
    if prepared.get("intent", "").startswith(("delete_", "update_")):
        if not prepared.get("target_preview"):
            return {
                "success": False,
                "text": "没有找到你在当前会话中可操作的有效流水。",
                "action": prepared["intent"],
            }
    pending = queue_action(
        prepared,
        sender_open_id,
        chat_id,
        source_message_id=context.get("message_id"),
    )
    _save_session(
        context,
        pending_action_id=pending["action_id"],
        pending_question=None,
        last_intent=prepared["intent"],
        history=[
            {
                "intent": prepared["intent"],
                "pending_action_id": pending["action_id"],
            }
        ],
    )
    return {
        "success": True,
        "text": _confirmation_text(prepared),
        "card": confirmation_card(pending),
        "action": prepared["intent"],
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
        "config": {"update_multi": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
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
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": "10 分钟内有效"}
                ],
            },
        ],
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
    preview = payload.get("target_preview") or {}
    if preview:
        target = (
            f"{preview.get('date')}｜{preview.get('category')}｜"
            f"¥{float(preview.get('amount') or 0):.2f}｜"
            f"{preview.get('description')}"
        )
    else:
        transaction_id = payload.get("transaction_id")
        target = (
            f"ID {transaction_id}"
            if transaction_id
            else "你在当前会话的上一笔有效流水"
        )
    if intent.startswith("delete_"):
        return f"将软删除：**{target}**"
    updates = payload.get("updates") or {}
    details = "\n".join(f"- {key}: {value}" for key, value in updates.items())
    return f"将修改：**{target}**\n{details or '- 未提供有效修改字段'}"


def sync_dashboard_card(dashboard, sync_result):
    configuration = dashboard.get("configuration") or {}
    fields = dashboard.get("fields") or {}
    counts = dashboard.get("counts") or {}
    recent_errors = dashboard.get("recent_errors") or []
    field_text = (
        "通过"
        if fields.get("success")
        else f"未通过：{fields.get('message') or '未知错误'}"
    )
    missing_fields = fields.get("missing_fields") or []
    if missing_fields:
        field_text += "\n缺少字段：" + "、".join(missing_fields)

    lines = [
        f"**机器人配置：** {'完整' if configuration.get('bot_ready') else '缺失'}",
        f"**多维表格配置：** {'完整' if configuration.get('bitable_ready') else '缺失'}",
        f"**字段状态：** {field_text}",
        "",
        f"**本地总流水：** {counts.get('total', 0)}",
        f"**已同步：** {counts.get('synced', 0)}",
        f"**待同步：** {counts.get('pending', 0)}",
        f"**失败：** {counts.get('failed', 0)}",
        f"**已写入 record_id：** {counts.get('record_id', 0)}",
        "",
    ]
    if sync_result.get("status_only"):
        lines.append("**本次操作：** 仅检查状态，未触发同步。")
    else:
        lines.append(
            f"**本次同步：** 成功 {sync_result.get('succeeded', 0)}，"
            f"失败 {sync_result.get('failed', 0)}"
        )
    if not sync_result.get("success"):
        lines.append(
            f"**本次错误：** code={sync_result.get('code', -1)}，"
            f"message={sync_result.get('message') or '未知错误'}，"
            f"log_id={sync_result.get('log_id') or '-'}"
        )
    if fields.get("code"):
        lines.append(
            f"**字段错误：** code={fields.get('code')}，"
            f"message={fields.get('message')}，"
            f"log_id={fields.get('log_id') or '-'}"
        )
    if recent_errors:
        latest = recent_errors[0]
        lines.append(
            f"**最近错误：** 本地ID {latest.get('local_id')}，"
            f"UID {latest.get('transaction_uid_prefix')}，"
            f"{latest.get('message')}"
        )
    lines.extend(
        [
            "",
            "需要全量同步或重置失败状态时，请前往 Streamlit 的“设置”页。",
        ]
    )
    success = bool(sync_result.get("success") and dashboard.get("success"))
    return {
        "schema": "2.0",
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "飞书多维表格同步状态",
            },
            "template": "green" if success else "orange",
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": "\n".join(lines)}
            ]
        },
    }


def _run_sync_status(sync_dashboard_callback=None):
    if sync_dashboard_callback is None:
        return {"success": False, "text": "多维表格同步状态当前不可用。", "action": "sync_status"}
    try:
        dashboard = sync_dashboard_callback()
    except Exception as exc:
        return {
            "success": False,
            "text": f"同步状态检查失败：{type(exc).__name__}。请运行 --check 查看详细诊断。",
            "action": "sync_status",
        }
    result = {
        "success": bool(dashboard.get("success")),
        "succeeded": 0,
        "failed": 0,
        "message": "同步状态检查完成。",
        "status_only": True,
    }
    return {
        "success": bool(dashboard.get("success")),
        "text": _format_sync_status_text(dashboard),
        "card": sync_dashboard_card(dashboard, result),
        "action": "sync_status",
    }


def _run_sync(sync_callback, sync_dashboard_callback=None):
    if sync_callback is None:
        return {"success": False, "text": "多维表格同步当前不可用。", "action": "sync"}
    try:
        result = sync_callback()
    except Exception as exc:
        return {
            "success": False,
            "text": f"多维表格同步失败：{type(exc).__name__}。请运行 --check 查看详细诊断。",
            "action": "sync",
        }
    dashboard = (
        sync_dashboard_callback()
        if sync_dashboard_callback is not None
        else {
            "success": bool(result.get("success")),
            "configuration": {},
            "fields": {},
            "counts": {},
            "recent_errors": [],
        }
    )
    return {
        "success": bool(result.get("success") and dashboard.get("success")),
        "text": result.get(
            "message",
            "同步任务已完成。" if result.get("success") else "同步失败，请运行 --check。",
        ),
        "card": sync_dashboard_card(dashboard, result),
        "action": "sync",
    }


def _format_sync_status_text(dashboard):
    counts = dashboard.get("counts") or {}
    fields = dashboard.get("fields") or {}
    return "\n".join(
        [
            "飞书多维表格同步状态",
            "",
            f"本地总流水：{counts.get('total', 0)}",
            f"已同步：{counts.get('synced', 0)}",
            f"待同步：{counts.get('pending', 0)}",
            f"失败：{counts.get('failed', 0)}",
            f"已写入 record_id：{counts.get('record_id', 0)}",
            f"字段状态：{fields.get('message') or '未检查'}",
        ]
    )


def _run_finance_analysis(focus="overview"):
    today = datetime.date.today()
    month = today.strftime("%Y-%m")
    overview = get_finance_overview(today.replace(day=1), today)
    categories = get_category_expense_summary(month)
    tags = get_tag_summary(month)
    warnings = get_budget_warning(month)
    insights = generate_finance_insights(month)
    return {
        "success": True,
        "text": _format_finance_analysis(
            month,
            overview,
            categories,
            tags,
            warnings,
            insights,
            focus=focus,
        ),
        "action": {
            "overview": "finance_analysis",
            "category": "category_rank",
            "budget": "budget_analysis",
            "tag": "tag_analysis",
        }.get(focus, "finance_analysis"),
    }


def _format_finance_analysis(
    month,
    overview,
    categories,
    tags,
    warnings,
    insights,
    focus="overview",
):
    titles = {
        "overview": "本月财务分析",
        "category": "本月支出分类排行",
        "budget": "本月预算情况",
        "tag": "本月标签分析",
    }
    lines = [
        f"{titles.get(focus, titles['overview'])}｜{month}",
        "",
        f"收入：¥{overview['total_income']:.2f}",
        f"支出：¥{overview['total_expense']:.2f}",
        f"结余：¥{overview['net_income']:.2f}",
        "最大支出分类："
        + (
            f"{categories[0]['category']}（¥{categories[0]['amount']:.2f}）"
            if categories
            else "暂无"
        ),
        "",
        "Top 3 支出分类：",
    ]
    lines.extend(
        (
            f"{index}. {item['category']} ¥{item['amount']:.2f}"
            f"（{item['share']:.1f}%）"
            for index, item in enumerate(categories[:3], 1)
        )
        if categories
        else ["暂无支出分类数据"]
    )
    lines.extend(["", "Top 5 标签消费场景："])
    lines.extend(
        (
            f"{index}. {item['tag']} ¥{item['amount']:.2f}"
            f"（{item['count']} 笔）"
            for index, item in enumerate(tags[:5], 1)
        )
        if tags
        else ["暂无标签消费数据"]
    )
    risky = [item for item in warnings if item.get("status") != "正常"]
    lines.extend(["", "预算预警："])
    lines.extend(
        (
            f"- {item['category']}：{item['status']}，"
            f"使用率 {item['usage_rate']:.1f}%"
            for item in sorted(
                risky,
                key=lambda row: row.get("usage_rate", 0),
                reverse=True,
            )[:5]
        )
        if risky
        else ["当前分类预算状态正常。"]
    )
    advice = str(insights.get("saving_advice") or "").strip()
    if advice:
        lines.extend(["", f"建议：{advice}"])
    return "\n".join(lines)


def _format_today(summary):
    lines = [
        f"今日账单｜{summary['date']}",
        "",
        f"收入：¥{summary['income']:.2f}",
        f"支出：¥{summary['expense']:.2f}",
        f"结余：¥{summary['balance']:.2f}",
        f"共 {summary['count']} 笔",
    ]
    if summary.get("is_over_daily_budget"):
        lines.append("今日支出高于当前日均预算参考，可以稍微留意。")
    return "\n".join(lines)


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
    if summary.get("budget_usage", 0) >= 80:
        lines.append("本月预算使用率较高，后续支出可以更谨慎一些。")
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


def _format_category(summary):
    text = (
        f"{summary['month']} {summary['category']}支出："
        f"¥{summary['amount']:.2f}，共 {summary['count']} 笔，"
        f"占本月支出的 {summary['share']:.1f}%。"
    )
    if summary.get("is_high"):
        text += " 这项支出占比较高，可以留意一下后续预算。"
    return text


def _handle_session_control(text, context, session):
    if not session:
        return None
    compact = re.sub(r"\s+", "", text)
    if compact in {"确认", "可以", "记上", "确定", "没问题", "就这样"}:
        return _resolve_pending_from_text("confirm", context, session)
    if compact in {"取消", "算了", "不记了", "不用了", "不要了"}:
        return _resolve_pending_from_text("cancel", context, session)
    return None


def _resolve_pending_from_text(operation, context, session):
    action_id = (session or {}).get("pending_action_id")
    if not action_id:
        pending = get_recent_pending_action(
            context.get("sender_open_id"),
            context.get("chat_id"),
        )
        action_id = pending["action_id"] if pending else None
    if not action_id:
        return {
            "success": False,
            "text": "当前没有等待确认的操作。",
            "action": operation,
        }
    result = resolve_action(
        action_id,
        operation,
        context.get("sender_open_id"),
        context.get("chat_id"),
    )
    return {
        "success": bool(result.get("success")),
        "text": result.get("message") or "已处理。",
        "card": result_card(result),
        "action": operation,
    }


def _ask_clarification(action, context, session):
    transactions = action.get("transactions") or []
    draft = transactions[0] if transactions else {}
    question = (
        action.get("clarification_question")
        or action.get("reply")
        or "这笔流水的金额是多少？"
    )
    history = list((session or {}).get("short_history") or [])
    history.append(
        {
            "intent": "ask_clarification",
            "draft_transaction": draft,
            "reply": question,
        }
    )
    _save_session(
        context,
        pending_action_id=None,
        pending_question=question,
        last_intent="ask_clarification",
        history=history,
    )
    return {
        "success": True,
        "text": question,
        "action": "clarification",
    }


def _revise_pending(action, context, session):
    action_id = (session or {}).get("pending_action_id")
    if not action_id:
        return {
            "success": False,
            "text": "当前没有可修改的待确认操作。",
            "action": "revise",
        }
    result = revise_pending_action(
        action_id,
        action.get("revision") or action.get("updates") or {},
        context.get("sender_open_id"),
        context.get("chat_id"),
    )
    if not result.get("success"):
        return {
            "success": False,
            "text": result.get("message"),
            "action": "revise",
        }
    pending = result["pending"]
    _save_session(
        context,
        pending_action_id=action_id,
        pending_question=None,
        last_intent="revise_pending_action",
        history=[
            {
                "intent": "revise_pending_action",
                "pending_action_id": action_id,
            }
        ],
    )
    return {
        "success": True,
        "text": (
            "已修改待确认内容，请再次确认。"
        ),
        "card": confirmation_card(pending),
        "action": "revise",
        "pending_action_id": action_id,
    }


def _load_session(context):
    if not context.get("sender_open_id") or not context.get("chat_id"):
        return None
    return get_feishu_session(
        context["sender_open_id"],
        context["chat_id"],
    )


def _save_session(
    context,
    pending_action_id=None,
    pending_question=None,
    last_intent=None,
    history=None,
):
    if not context.get("sender_open_id") or not context.get("chat_id"):
        return None
    return save_feishu_session(
        context["sender_open_id"],
        context["chat_id"],
        short_history=history or [],
        pending_action_id=pending_action_id,
        pending_question=pending_question,
        last_intent=last_intent,
    )


def _parser_context(session):
    session = session or {}
    history = list(session.get("short_history") or [])
    draft = None
    for item in reversed(history):
        if item.get("draft_transaction"):
            draft = item["draft_transaction"]
            break
    return {
        "pending_question": session.get("pending_question"),
        "pending_action_id": session.get("pending_action_id"),
        "last_intent": session.get("last_intent"),
        "short_history": history,
        "draft_transaction": draft,
    }


def _call_parser(parser, text, context):
    try:
        return parser(text, context=context)
    except TypeError as exc:
        if "context" not in str(exc):
            raise
        return parser(text)


def _confirmation_text(action):
    if action.get("intent") == "create_transactions":
        rows = action.get("transactions") or []
        if len(rows) == 1:
            row = rows[0]
            return (
                f"识别为 {row['date']} {row['category']}"
                f"{row['type']} ¥{float(row['amount']):.2f}，"
                "是否确认？"
            )
        return f"识别到 {len(rows)} 笔流水，是否确认？"
    if action.get("intent", "").startswith("delete_"):
        return "将删除这笔流水，是否确认？"
    return "将修改这笔流水，是否确认？"


def _parse_failure_text():
    return """没有可靠识别出你的操作。
示例：
午饭25
昨天打车36.5，晚饭42
今日账单
撤销上一笔"""

import datetime
import hashlib
import json
import logging
import os
import re

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    from .config import PROJECT_ROOT, load_env_file
    from .ledger import CATEGORIES, classify_text, parse_entry_text
    from .tagging import generate_tags
except ImportError:
    from config import PROJECT_ROOT, load_env_file
    from ledger import CATEGORIES, classify_text, parse_entry_text
    from tagging import generate_tags


LOGGER = logging.getLogger("finance_tracker.ai_parser")
INTENTS = {
    "create_transactions",
    "ask_clarification",
    "revise_pending_action",
    "confirm_pending_action",
    "cancel_pending_action",
    "query_today_summary",
    "query_month_summary",
    "query_category_summary",
    "query_recent_transactions",
    "delete_last_transaction",
    "delete_transaction_by_id",
    "update_last_transaction",
    "update_transaction_by_id",
    "generate_report",
    "sync_bitable",
    "chat",
    "help",
    "unknown",
}
MUTATING_INTENTS = {
    "create_transactions",
    "delete_last_transaction",
    "delete_transaction_by_id",
    "update_last_transaction",
    "update_transaction_by_id",
}
CONFIRM_WORDS = {"确认", "可以", "记上", "确定", "没问题", "就这样"}
CANCEL_WORDS = {"取消", "算了", "不记了", "不用了", "不要了"}


def get_ai_parser_config():
    load_env_file(PROJECT_ROOT / ".env", override=False)
    return {
        "enabled": _as_bool(os.getenv("AI_PARSER_ENABLED"), False),
        "require_confirmation": _as_bool(
            os.getenv("AI_PARSER_REQUIRE_CONFIRMATION"), True
        ),
        "fallback_to_local": _as_bool(
            os.getenv("AI_PARSER_FALLBACK_TO_LOCAL"), True
        ),
        "api_key": os.getenv("DEEPSEEK_API_KEY", "").strip(),
        "base_url": os.getenv(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
        ).strip(),
        "model": os.getenv(
            "DEEPSEEK_MODEL", "deepseek-v4-flash"
        ).strip(),
        "timeout": max(
            1,
            min(
                int(
                    os.getenv(
                        "AI_PARSER_TIMEOUT_SECONDS", "15"
                    ) or "15"
                ),
                60,
            ),
        ),
    }


def parse_action(
    text,
    default_date=None,
    client=None,
    config=None,
    context=None,
):
    config = config or get_ai_parser_config()
    text = str(text or "").strip()
    base_date = _as_date(
        default_date or datetime.date.today()
    ).isoformat()
    safe_context = _safe_context(context)
    if not text:
        return _unknown("empty")

    if not config["enabled"] or not config["api_key"]:
        return _local_action(
            text, base_date, "disabled_or_unconfigured", safe_context
        )

    try:
        ai_client = client or _build_client(config)
        response = ai_client.chat.completions.create(
            model=config["model"],
            timeout=config["timeout"],
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _system_prompt(base_date)},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "message": text,
                            "conversation_context": safe_context,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        raw = response.choices[0].message.content
        action = validate_action(json.loads(raw), base_date)
        _audit(text, action, "ai")
        if (
            action["intent"] == "ask_clarification"
            and parse_entry_text(text, base_date)
        ):
            return _local_action(
                text,
                base_date,
                "ai_unnecessary_clarification",
                safe_context,
            )
        if (
            action["confidence"] < 0.65
            and action["intent"] not in {
                "ask_clarification", "chat"
            }
        ):
            return _local_action(
                text, base_date, "low_confidence", safe_context
            )
        action["parser"] = "ai"
        action["need_confirmation"] = bool(
            action["intent"] in MUTATING_INTENTS
            and config["require_confirmation"]
        )
        return action
    except Exception as exc:
        LOGGER.warning(
            "AI parser failed: error_type=%s",
            type(exc).__name__,
        )
        if config["fallback_to_local"]:
            return _local_action(
                text, base_date, "ai_error", safe_context
            )
        return _unknown("ai_error")


def validate_action(value, default_date=None):
    if not isinstance(value, dict):
        raise ValueError("AI response must be an object.")
    intent = str(value.get("intent") or "unknown")
    if intent not in INTENTS:
        raise ValueError("Unsupported intent.")
    confidence = float(value.get("confidence", 0))
    if not 0 <= confidence <= 1:
        raise ValueError("Confidence must be between 0 and 1.")

    raw_transactions = value.get("transactions") or []
    if not isinstance(raw_transactions, list):
        raise ValueError("transactions must be a list.")
    allow_partial = intent == "ask_clarification"
    transactions = [
        _validate_transaction(
            item,
            default_date,
            allow_partial=allow_partial,
        )
        for item in raw_transactions
    ]
    if intent == "create_transactions" and not transactions:
        raise ValueError("Create intent requires transactions.")

    transaction_id = value.get("transaction_id")
    if transaction_id not in (None, ""):
        transaction_id = int(transaction_id)
        if transaction_id <= 0:
            raise ValueError("transaction_id must be positive.")

    revision = _validate_updates(
        value.get("revision") or value.get("updates") or {}
    )
    query = value.get("query") or {}
    if not isinstance(query, dict):
        raise ValueError("query must be an object.")
    limit = int(query.get("limit") or value.get("limit") or 5)
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50.")
    category = query.get("category")
    if category and category not in CATEGORIES:
        category = "其他"

    return {
        "intent": intent,
        "confidence": confidence,
        "reply": str(value.get("reply") or "")[:300],
        "clarification_question": str(
            value.get("clarification_question") or ""
        )[:200],
        "transactions": transactions,
        "transaction_id": transaction_id,
        "revision": revision,
        "updates": revision,
        "query": {
            "period": str(query.get("period") or "")[:30],
            "category": category,
            "limit": limit,
        },
        "limit": limit,
        "requires_confirmation": bool(
            value.get(
                "requires_confirmation",
                intent in MUTATING_INTENTS,
            )
        ),
        "reason": str(value.get("reason") or "")[:200],
    }


def _validate_transaction(item, default_date, allow_partial=False):
    if not isinstance(item, dict):
        raise ValueError("Each transaction must be an object.")
    raw_amount = item.get("amount")
    amount = None if raw_amount in (None, "") else float(raw_amount)
    if amount is not None and not 0 < amount <= 100000000:
        raise ValueError("Invalid transaction amount.")
    if amount is None and not allow_partial:
        raise ValueError("Transaction amount is required.")

    date_value = _as_date(
        item.get("date") or default_date
    ).isoformat()
    txn_type = str(item.get("type") or "支出")
    if txn_type not in {"支出", "收入"}:
        raise ValueError("Invalid transaction type.")
    category = str(item.get("category") or "其他")
    if category not in CATEGORIES:
        category = "其他"
    description = str(item.get("description") or category).strip()
    if not description:
        description = category
    default_need = txn_type == "支出" and category in {
        "餐饮", "交通", "居住", "医疗", "教育"
    }
    is_need = int(bool(item.get("is_need", default_need)))
    is_fixed = int(bool(item.get("is_fixed", False)))
    tags = generate_tags(
        {
            "date": date_value,
            "type": txn_type,
            "category": category,
            "amount": amount,
            "description": description,
            "tags": item.get("tags") or [],
            "is_need": is_need,
            "is_fixed": is_fixed,
        }
    )
    return {
        "date": date_value,
        "type": txn_type,
        "category": category,
        "amount": amount,
        "description": description[:200],
        "tags": str(tags)[:200],
        "is_need": is_need,
        "is_fixed": is_fixed,
    }


def _validate_updates(updates):
    if not isinstance(updates, dict):
        raise ValueError("revision must be an object.")
    allowed = {
        "date", "type", "category", "amount", "description",
        "tags", "is_need", "is_fixed",
    }
    clean = {
        key: value
        for key, value in updates.items()
        if key in allowed and value not in (None, "")
    }
    if "date" in clean:
        clean["date"] = _as_date(clean["date"]).isoformat()
    if "type" in clean and clean["type"] not in {"支出", "收入"}:
        raise ValueError("Invalid update type.")
    if "category" in clean and clean["category"] not in CATEGORIES:
        clean["category"] = "其他"
    if "amount" in clean:
        clean["amount"] = float(clean["amount"])
        if clean["amount"] <= 0:
            raise ValueError("Invalid update amount.")
    if "description" in clean:
        clean["description"] = str(clean["description"]).strip()[:200]
    return clean


def _local_action(text, base_date, reason, context=None):
    context = context or {}
    compact = re.sub(r"\s+", "", text)
    if compact in CONFIRM_WORDS:
        return _simple_action("confirm_pending_action", reason)
    if compact in CANCEL_WORDS:
        return _simple_action("cancel_pending_action", reason)

    revision = _local_revision(text)
    if revision and context.get("pending_action_id"):
        action = _simple_action("revise_pending_action", reason)
        action["revision"] = revision
        action["updates"] = revision
        return action

    draft = context.get("draft_transaction")
    amount_only = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:元)?\s*", text)
    if draft and amount_only:
        transaction = dict(draft)
        transaction["amount"] = float(amount_only.group(1))
        action = _simple_action("create_transactions", reason)
        action["transactions"] = [
            _validate_transaction(transaction, base_date)
        ]
        action["need_confirmation"] = True
        return action

    query_action = _local_query_action(text, reason)
    if query_action:
        return query_action

    mutation = _local_mutation_action(text, reason)
    if mutation:
        _audit(text, mutation, "local")
        return mutation

    records = parse_entry_text(text, base_date)
    if records:
        action = _simple_action("create_transactions", reason)
        action["confidence"] = 0.8
        action["transactions"] = records
        action["need_confirmation"] = True
    elif _looks_like_transaction_without_amount(text):
        txn_type, category = classify_text(text)
        action = _simple_action("ask_clarification", reason)
        action["confidence"] = 0.8
        action["clarification_question"] = (
            f"这笔{category}{txn_type}金额是多少？"
        )
        action["transactions"] = [
            {
                "date": base_date,
                "type": txn_type,
                "category": category,
                "amount": None,
                "description": _clean_draft_description(text, category),
                "tags": "",
                "is_need": 0,
                "is_fixed": 0,
            }
        ]
    else:
        action = _unknown(reason)
    _audit(text, action, "local")
    return action


def _local_query_action(text, reason):
    compact = re.sub(r"\s+", "", text)
    recent = re.search(r"最近(\d+)笔", compact)
    if recent:
        action = _simple_action("query_recent_transactions", reason)
        action["limit"] = max(1, min(int(recent.group(1)), 50))
        action["query"]["limit"] = action["limit"]
        return action
    category = next(
        (item for item in CATEGORIES if item in compact),
        None,
    )
    if category and any(
        word in compact for word in ("花了多少", "支出多少", "消费多少")
    ):
        action = _simple_action("query_category_summary", reason)
        action["query"] = {
            "period": "month",
            "category": category,
            "limit": 5,
        }
        return action
    if any(
        phrase in compact
        for phrase in ("今天花了多少", "今日支出", "今天有没有超支")
    ):
        return _simple_action("query_today_summary", reason)
    if any(
        phrase in compact
        for phrase in (
            "这个月支出了多少", "本月支出", "这个月收入多少",
            "本月收入", "这个月消费情况", "本月账单",
        )
    ):
        return _simple_action("query_month_summary", reason)
    if compact in {"生成今日日报", "生成日报"}:
        return _simple_action("generate_report", reason)
    if compact == "同步看板":
        return _simple_action("sync_bitable", reason)
    return None


def _local_mutation_action(text, reason):
    if re.fullmatch(
        r"(?:撤销|删除)?\s*(?:上一笔|刚才那笔)\s*(?:删掉|删除)?",
        text,
    ):
        return _mutation_action("delete_last_transaction", reason)
    delete_by_id = re.fullmatch(
        r"(?:撤销|删除)\s*(?:ID|id|第)?\s*(\d+)\s*(?:笔)?",
        text,
    )
    if delete_by_id:
        action = _mutation_action("delete_transaction_by_id", reason)
        action["transaction_id"] = int(delete_by_id.group(1))
        return action

    target_match = re.search(r"(?:ID|id)\s*(\d+)|上一笔|刚才那笔", text)
    if not target_match or not any(
        word in text for word in ("改", "修改", "更正")
    ):
        return None
    updates = _local_revision(text)
    if not updates:
        return None
    transaction_id = (
        int(target_match.group(1))
        if target_match.group(1)
        else None
    )
    intent = (
        "update_transaction_by_id"
        if transaction_id is not None
        else "update_last_transaction"
    )
    action = _mutation_action(intent, reason)
    action["transaction_id"] = transaction_id
    action["updates"] = updates
    action["revision"] = updates
    return action


def _local_revision(text):
    updates = {}
    amount_match = re.search(
        r"(?:不是\s*\d+(?:\.\d+)?\s*[,，]?\s*(?:是|改成|改为)|"
        r"金额(?:改成|改为|为|是)?|改成|改为)\s*(\d+(?:\.\d+)?)",
        text,
    )
    if amount_match:
        updates["amount"] = float(amount_match.group(1))
    category_match = re.search(
        r"分类(?:改成|改为|为|是)?\s*([\u4e00-\u9fff]{2,4})",
        text,
    )
    if category_match and category_match.group(1) in CATEGORIES:
        updates["category"] = category_match.group(1)
    description_match = re.search(
        r"(?:说明|备注)(?:改成|改为|为|是)?\s*([^，,。]+)",
        text,
    )
    if description_match:
        updates["description"] = description_match.group(1).strip()[:200]
    return updates


def _simple_action(intent, reason):
    return {
        "intent": intent,
        "confidence": 0.9,
        "reply": "",
        "clarification_question": "",
        "transactions": [],
        "transaction_id": None,
        "revision": {},
        "updates": {},
        "query": {"period": "", "category": None, "limit": 5},
        "limit": 5,
        "requires_confirmation": intent in MUTATING_INTENTS,
        "reason": reason,
        "parser": "local",
        "need_confirmation": intent in MUTATING_INTENTS,
    }


def _mutation_action(intent, reason):
    return _simple_action(intent, reason)


def _unknown(reason):
    action = _simple_action("unknown", reason)
    action["confidence"] = 0.0
    action["parser"] = "none"
    return action


def _safe_context(context):
    context = context or {}
    draft = context.get("draft_transaction")
    safe_draft = None
    if isinstance(draft, dict):
        safe_draft = {
            key: draft.get(key)
            for key in (
                "date", "type", "category", "amount",
                "description", "tags", "is_need", "is_fixed",
            )
        }
    return {
        "pending_question": str(
            context.get("pending_question") or ""
        )[:160],
        "pending_action_id": bool(context.get("pending_action_id")),
        "last_intent": str(context.get("last_intent") or "")[:80],
        "draft_transaction": safe_draft,
        "short_history": list(context.get("short_history") or [])[-4:],
    }


def _looks_like_transaction_without_amount(text):
    if re.search(r"\d+(?:\.\d+)?", text):
        return False
    keywords = (
        "吃饭", "午饭", "晚饭", "早餐", "咖啡", "打车",
        "买了", "花了", "收入", "工资", "奖金", "报销",
    )
    return any(keyword in text for keyword in keywords)


def _clean_draft_description(text, category):
    value = re.sub(
        r"今天|昨天|刚才|了|一笔|再加",
        "",
        str(text),
    ).strip(" ，,。.；;")
    return value or category


def _build_client(config):
    if OpenAI is None:
        raise RuntimeError("openai package is not installed.")
    return OpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"],
    )


def _audit(text, action, parser):
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    LOGGER.info(
        "intent_parse text_hash=%s text_length=%d parser=%s "
        "intent=%s confidence=%.2f",
        digest,
        len(text),
        parser,
        action.get("intent", "unknown"),
        float(action.get("confidence", 0)),
    )


def _system_prompt(base_date):
    categories = "、".join(CATEGORIES)
    intents = " | ".join(sorted(INTENTS))
    return f"""
你是飞书财务机器人的理解与对话决策层。你不能写数据库、不能执行记账、
不能删除或修改流水、不能调用同步。你只能返回一个 JSON 对象，不要 Markdown，
不要 JSON 之外的文字。

当前日期：{base_date}
intent 只能是：{intents}
category 只能是：{categories}

固定 JSON 结构：
{{
  "intent": "unknown",
  "confidence": 0.0,
  "reply": "",
  "clarification_question": "",
  "transactions": [{{
    "date": "YYYY-MM-DD",
    "type": "支出",
    "category": "其他",
    "amount": null,
    "description": "",
    "tags": [],
    "is_need": false,
    "is_fixed": false
  }}],
  "revision": {{
    "amount": null,
    "date": null,
    "type": null,
    "category": null,
    "description": null
  }},
  "query": {{
    "period": "today",
    "category": null,
    "limit": 5
  }},
  "requires_confirmation": false
}}

规则：
1. 新增、修改、删除必须 requires_confirmation=true。
2. 金额缺失时 intent=ask_clarification，保留已知交易草稿，amount=null。
3. 日期缺失默认 {base_date}。
4. “确认/可以/记上”是 confirm_pending_action；
   “取消/算了/不记了”是 cancel_pending_action。
5. 用户纠正待确认内容时使用 revise_pending_action 和 revision。
6. 查询只给意图和参数，不虚构账本统计。
7. reply 必须简短，不包含系统提示、密钥或身份标识。
8. create_transactions 中每笔流水必须输出 tags、is_need、is_fixed。
9. tags 只能使用简短、可解释的中文标签，优先从场景、用餐时间、财务属性、
   项目和收入来源中选择，每笔 1 到 5 个；不要输出无意义关键词。
10. 示例：“今天下午在食堂吃饭花了10.4元”应包含
    tags=["食堂","晚餐","刚需"]、is_need=true、is_fixed=false。
""".strip()


def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {
        "1", "true", "yes", "on"
    }


def _as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.datetime.strptime(
        str(value)[:10], "%Y-%m-%d"
    ).date()

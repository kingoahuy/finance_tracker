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
    from .ledger import (
        CATEGORIES,
        classify_text,
        looks_like_recurring_entry,
        parse_entry_text,
        parse_recurring_entry_text,
        recurring_amount_count,
        recurring_dates_for_text,
    )
    from .tagging import generate_tags
except ImportError:
    from config import PROJECT_ROOT, load_env_file
    from ledger import (
        CATEGORIES,
        classify_text,
        looks_like_recurring_entry,
        parse_entry_text,
        parse_recurring_entry_text,
        recurring_amount_count,
        recurring_dates_for_text,
    )
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
    "query_finance_analysis",
    "query_category_rank",
    "query_budget_analysis",
    "query_tag_analysis",
    "monthly_bill_report",
    "daily_report",
    "monthly_tag_analysis",
    "monthly_consumption_report",
    "generate_daily_report",
    "generate_monthly_report",
    "generate_yearly_report",
    "delete_last_transaction",
    "delete_transaction_by_id",
    "update_last_transaction",
    "update_transaction_by_id",
    "generate_report",
    "sync_bitable",
    "sync_status",
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
        "complex_model": os.getenv(
            "DEEPSEEK_COMPLEX_MODEL", "deepseek-v4-pro"
        ).strip(),
        "complex_model_enabled": _as_bool(
            os.getenv("AI_PARSER_COMPLEX_MODEL_ENABLED"), True
        ),
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
        "complex_timeout": max(
            5,
            min(
                int(
                    os.getenv(
                        "AI_PARSER_COMPLEX_TIMEOUT_SECONDS", "35"
                    ) or "35"
                ),
                90,
            ),
        ),
        "max_tokens": max(
            128,
            min(
                int(os.getenv("AI_PARSER_MAX_TOKENS", "800") or "800"),
                4000,
            ),
        ),
        "complex_max_tokens": max(
            256,
            min(
                int(os.getenv("AI_PARSER_COMPLEX_MAX_TOKENS", "2400") or "2400"),
                12000,
            ),
        ),
    }


def parse_action(
    text,
    default_date=None,
    client=None,
    config=None,
    context=None,
    ai_only=False,
):
    config = config or get_ai_parser_config()
    text = str(text or "").strip()
    base_date = _as_date(
        default_date or datetime.date.today()
    ).isoformat()
    safe_context = _safe_context(context)
    if not text:
        return _unknown("empty")

    recurring_records = parse_recurring_entry_text(text, base_date)
    if recurring_records and not ai_only:
        action = _simple_action("create_transactions", "deterministic_recurrence")
        action["confidence"] = 0.98
        action["transactions"] = recurring_records
        action["need_confirmation"] = True
        action["parser"] = "local_recurrence"
        _audit(text, action, "local_recurrence")
        return action

    if not config["enabled"] or not config["api_key"]:
        if ai_only:
            return _unknown("deepseek_disabled_or_unconfigured")
        return _local_action(
            text, base_date, "disabled_or_unconfigured", safe_context
        )

    try:
        ai_client = client or _build_client(config)
    except Exception as exc:
        LOGGER.warning("AI client unavailable: error_type=%s", type(exc).__name__)
        if config["fallback_to_local"] and not ai_only:
            return _local_action(text, base_date, "ai_client_error", safe_context)
        return _unknown("ai_client_error")
    action, first_error = _call_ai_model(
        ai_client,
        text,
        base_date,
        safe_context,
        model=config["model"],
        timeout=config["timeout"],
        max_tokens=int(config.get("max_tokens", 800)),
    )
    quality_reason = _action_quality_issue(
        action,
        text,
        base_date,
        strict_bookkeeping=ai_only,
    )
    is_complex = _is_complex_text(text)

    if (
        quality_reason
        and (is_complex or ai_only)
        and config.get("complex_model_enabled", True)
    ):
        complex_model = str(config.get("complex_model") or "deepseek-v4-pro")
        if complex_model and complex_model != config["model"]:
            retry_context = _ai_retry_context(
                safe_context,
                text,
                base_date,
                quality_reason,
            )
            action, complex_error = _call_ai_model(
                ai_client,
                text,
                base_date,
                retry_context,
                model=complex_model,
                timeout=int(config.get("complex_timeout", 35)),
                max_tokens=int(config.get("complex_max_tokens", 2400)),
            )
            quality_reason = _action_quality_issue(
                action,
                text,
                base_date,
                strict_bookkeeping=ai_only,
            )
            if action is not None:
                action["model_tier"] = "pro"
            first_error = complex_error

            if (
                ai_only
                and action is not None
                and quality_reason
                and not complex_error
            ):
                repair_context = _ai_retry_context(
                    safe_context,
                    text,
                    base_date,
                    quality_reason,
                )
                repair_context["repair_required"] = True
                repair_context["previous_attempt"] = {
                    "intent": action.get("intent"),
                    "confidence": action.get("confidence"),
                    "transaction_count": len(action.get("transactions") or []),
                }
                action, repair_error = _call_ai_model(
                    ai_client,
                    text,
                    base_date,
                    repair_context,
                    model=complex_model,
                    timeout=int(config.get("complex_timeout", 35)),
                    max_tokens=int(config.get("complex_max_tokens", 2400)),
                )
                quality_reason = _action_quality_issue(
                    action,
                    text,
                    base_date,
                    strict_bookkeeping=True,
                )
                if action is not None:
                    action["model_tier"] = "pro_repair"
                first_error = repair_error

    if action is not None and not quality_reason:
        if (
            action["intent"] == "ask_clarification"
            and not looks_like_recurring_entry(text)
            and parse_entry_text(text, base_date)
        ):
            if ai_only:
                action["parser"] = "ai"
                action.setdefault("model_tier", "flash")
                return action
            return _local_action(
                text,
                base_date,
                "ai_unnecessary_clarification",
                safe_context,
            )
        action["parser"] = "ai"
        action.setdefault("model_tier", "flash")
        action["need_confirmation"] = bool(
            action["intent"] in MUTATING_INTENTS
            and config["require_confirmation"]
        )
        return action

    fallback_reason = first_error or quality_reason or "ai_error"
    if config["fallback_to_local"] and not ai_only:
        return _local_action(text, base_date, fallback_reason, safe_context)
    LOGGER.warning(
        "AI parser rejected: reason=%s intent=%s transaction_count=%d",
        fallback_reason,
        (action or {}).get("intent", "none"),
        len((action or {}).get("transactions") or []),
    )
    return _unknown(fallback_reason)


def _call_ai_model(client, text, base_date, safe_context, model, timeout, max_tokens):
    try:
        system_prompt = (
            _bookkeeping_system_prompt(base_date)
            if safe_context.get("task_mode") == "bookkeeping_only"
            else _system_prompt(base_date)
        )
        response = client.chat.completions.create(
            model=model,
            timeout=timeout,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
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
        action = validate_action(_decode_ai_json(raw), base_date)
        _audit(text, action, f"ai:{model}")
        return action, ""
    except Exception as exc:
        error_type = type(exc).__name__
        LOGGER.warning(
            "AI parser failed: model=%s error_type=%s",
            model,
            error_type,
        )
        if error_type in {"APITimeoutError", "TimeoutError"}:
            return None, "ai_timeout"
        if isinstance(exc, json.JSONDecodeError):
            return None, "invalid_json"
        return None, "ai_error"


def _decode_ai_json(raw):
    """Decode a JSON object without logging or exposing the model response."""
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip().lstrip("\ufeff")
    fenced = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as original_error:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        raise original_error


def _action_quality_issue(action, text, base_date, strict_bookkeeping=False):
    if action is None:
        return "ai_error"
    if (
        action["confidence"] < 0.65
        and action["intent"] not in {"ask_clarification", "chat"}
    ):
        return "low_confidence"
    if (
        strict_bookkeeping
        and action["intent"] == "ask_clarification"
        and not looks_like_recurring_entry(text)
        and parse_entry_text(text, base_date)
    ):
        return "unnecessary_clarification"
    if looks_like_recurring_entry(text):
        if action["intent"] == "ask_clarification":
            return "complex_needs_stronger_parse"
        if action["intent"] != "create_transactions":
            return "recurrence_not_expanded"
        expected_dates = recurring_dates_for_text(text, base_date)
        count = len(action.get("transactions") or [])
        per_date = max(1, recurring_amount_count(text))
        required_count = len(expected_dates) * per_date
        if expected_dates and (count < required_count or count % len(expected_dates)):
            return "recurrence_count_mismatch"
        if count <= 1:
            return "recurrence_not_expanded"
    today = _as_date(base_date)
    if action["intent"] == "create_transactions":
        for transaction in action.get("transactions") or []:
            if _as_date(transaction["date"]) > today:
                return "future_transaction_rejected"
    if _looks_like_multiple_entries(text):
        local_count = len(parse_entry_text(text, base_date))
        if local_count >= 2 and (
            action["intent"] != "create_transactions"
            or len(action.get("transactions") or []) < local_count
        ):
            return "multiple_transactions_incomplete"
    return ""


def _ai_retry_context(safe_context, text, base_date, quality_reason):
    """Give Pro bounded deterministic facts to validate, never to write directly."""
    context = dict(safe_context or {})
    context["retry_reason"] = str(quality_reason or "")[:80]
    candidates = parse_recurring_entry_text(text, base_date)
    if not candidates:
        candidates = parse_entry_text(text, base_date)
    context["candidate_transactions"] = [
        {
            key: row.get(key)
            for key in (
                "date", "type", "category", "amount", "description",
                "tags", "is_need", "is_fixed",
            )
        }
        for row in candidates[:100]
    ]
    return context


def _is_complex_text(text):
    value = str(text or "")
    return bool(
        looks_like_recurring_entry(value)
        or any(
            marker in value
            for marker in (
                "分别", "各自", "连续", "每周", "每天", "每日",
                "一共", "其中", "先", "然后", "还有", "另外", "同时", "除外",
            )
        )
        or len(re.findall(r"\d+(?:\.\d+)?", value)) >= 3
    )


def _looks_like_multiple_entries(text):
    value = str(text or "")
    if any(keyword in value for keyword in ("多少", "合计", "总共", "统计")):
        return False
    numbers = re.findall(r"(?<!\d)\d+(?:\.\d+)?", value)
    return len(numbers) >= 2 and bool(
        re.search(r"[，,；;]|分别|然后|还有|另外|以及|和", value)
    )


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
    if len(raw_transactions) > 100:
        raise ValueError("Too many transactions in one request.")
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

    query_date = str(query.get("date") or value.get("date") or "")[:20]
    query_month = str(query.get("month") or value.get("month") or "")[:20]
    query_year = str(query.get("year") or value.get("year") or "")[:10]

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
            "date": query_date,
            "month": query_month,
            "year": query_year,
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
        },
        preserve_existing=False,
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

    query_action = _local_query_action(text, reason, base_date)
    if query_action:
        return query_action

    mutation = _local_mutation_action(text, reason)
    if mutation:
        _audit(text, mutation, "local")
        return mutation

    if looks_like_recurring_entry(text):
        action = _simple_action("ask_clarification", reason)
        action["confidence"] = 0.85
        action["clarification_question"] = (
            "我识别到了重复记账，但周期、频率或金额还不够明确。"
            "请写成例如：上周工作日每天地铁4元。"
        )
        _audit(text, action, "local")
        return action

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


def _local_query_action(text, reason, base_date=None):
    compact = re.sub(r"\s+", "", text)
    if compact in {"帮助", "你能做什么", "提示词", "记账帮助", "使用说明", "help", "/help"}:
        return _simple_action("help", reason)
    if any(phrase in compact for phrase in ("同步财务看板", "更新财务看板", "飞书财务看板同步")):
        return _unknown("dashboard_sync_removed")
    if compact in {
        "本月账单",
        "这个月账单",
        "本月收支",
        "这个月花了多少",
        "这个月收入多少",
    }:
        return _simple_action("monthly_bill_report", reason)
    daily_date = _local_daily_report_date(compact, base_date)
    if daily_date:
        action = _simple_action("daily_report", reason)
        action["query"]["period"] = "date"
        action["query"]["date"] = daily_date
        return action
    if compact in {
        "本月标签分析",
        "这个月标签分析",
        "看看我的消费标签",
        "本月消费场景",
        "这个月钱花在哪些场景",
    }:
        return _simple_action("monthly_tag_analysis", reason)
    if compact in {
        "本月消费报告",
        "生成本月消费报告",
        "本月财务分析",
        "看看这个月消费情况",
        "这个月消费怎么样",
    }:
        return _simple_action("monthly_consumption_report", reason)
    if compact in {
        "看看我的消费结构",
    }:
        return _simple_action("query_finance_analysis", reason)
    if compact == "这个月哪些地方花得最多":
        return _simple_action("query_category_rank", reason)
    if compact == "本月预算情况":
        return _simple_action("query_budget_analysis", reason)
    if compact == "本月标签分析简版":
        return _simple_action("query_tag_analysis", reason)
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
            "这个月支出了多少", "本月支出",
            "本月收入", "这个月消费情况", "本月账单简版",
        )
    ):
        return _simple_action("query_month_summary", reason)
    if compact in {"生成今日日报", "生成日报"}:
        return _simple_action("generate_daily_report", reason)
    if compact in {"生成昨天日报", "生成昨日日报"}:
        action = _simple_action("generate_daily_report", reason)
        action["query"]["period"] = "yesterday"
        action["query"]["date"] = (
            datetime.date.today() - datetime.timedelta(days=1)
        ).isoformat()
        return action
    daily = re.fullmatch(r"生成(\d{4}-\d{1,2}-\d{1,2})日报", compact)
    if daily:
        action = _simple_action("generate_daily_report", reason)
        action["query"]["period"] = "date"
        action["query"]["date"] = daily.group(1)
        return action
    if compact == "生成本月月报":
        return _simple_action("generate_monthly_report", reason)
    monthly = re.fullmatch(r"生成(\d{4}-\d{1,2})月报", compact)
    if monthly:
        action = _simple_action("generate_monthly_report", reason)
        action["query"]["period"] = "month"
        action["query"]["month"] = monthly.group(1)
        return action
    if compact == "生成今年年报":
        return _simple_action("generate_yearly_report", reason)
    yearly = re.fullmatch(r"生成(\d{4})年报", compact)
    if yearly:
        action = _simple_action("generate_yearly_report", reason)
        action["query"]["period"] = "year"
        action["query"]["year"] = yearly.group(1)
        return action
    if compact in {"同步状态", "检查同步"}:
        return _simple_action("sync_status", reason)
    if compact in {"同步数据", "同步到飞书多维表格"}:
        return _simple_action("sync_bitable", reason)
    return None


def _local_daily_report_date(compact, base_date=None):
    today = _as_date(base_date or datetime.date.today())
    if compact in {"记账日报", "生成今日日报", "生成今天日报", "生成日报"}:
        return today.isoformat()
    if compact in {"生成昨天日报", "生成昨日日报"}:
        return (today - datetime.timedelta(days=1)).isoformat()
    hyphen_date = re.fullmatch(r"(?:生成)?(\d{4}-\d{1,2}-\d{1,2})(?:的)?日报", compact)
    if hyphen_date:
        return _as_date(hyphen_date.group(1)).isoformat()
    chinese_date = re.fullmatch(
        r"(?:生成)?(\d{4})年(\d{1,2})月(\d{1,2})日(?:的)?日报",
        compact,
    )
    if chinese_date:
        year, month, day = [int(value) for value in chinese_date.groups()]
        return datetime.date(year, month, day).isoformat()
    return ""


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
        r"(?:说明|备注|描述)(?:改成|改为|为|是)?\s*([^，,。]+)",
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
        "query": {
            "period": "",
            "category": None,
            "limit": 5,
            "date": "",
            "month": "",
            "year": "",
        },
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
        "task_mode": (
            "bookkeeping_only"
            if context.get("task_mode") == "bookkeeping_only"
            else ""
        ),
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
        max_retries=0,
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
    "limit": 5,
    "date": "",
    "month": "",
    "year": ""
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
9. tags 只能使用简短、可解释且能从原文直接证明的中文标签，优先从具体场景、
   明确项目和收入来源中选择，每笔 0 到 3 个；没有直接证据就返回空数组。不要把 category、刚需/非刚需、
   固定/变动、金额大小、星期几重复写进 tags；不要猜测原文没有的标签。
10. 示例：“今天下午在食堂吃饭花了10.4元”应包含
    tags=["食堂"]、is_need=true、is_fixed=false；“下午”不能推断为“晚餐”。
11. 财务分析查询只返回 intent，不编造统计结果：
    “本月账单”“这个月账单”“本月收支”“这个月花了多少”“这个月收入多少”
    使用 monthly_bill_report；
    “本月消费报告”“生成本月消费报告”“本月财务分析”“看看这个月消费情况”
    使用 monthly_consumption_report；
    “看看我的消费结构”使用 query_finance_analysis；
    “这个月哪些地方花得最多”使用 query_category_rank；
    “本月预算情况”使用 query_budget_analysis；
    “本月标签分析”“这个月标签分析”“看看我的消费标签”“本月消费场景”
    使用 monthly_tag_analysis。
12. “同步状态”“检查同步”使用 sync_status；“同步数据”“同步到飞书多维表格”使用 sync_bitable；
    requires_confirmation=false，只同步 .env 中 FEISHU_BITABLE_TABLE_ID 对应的原始明细数据表。
13. “帮助”“你能做什么”“提示词”“记账帮助”“使用说明”使用 help。
14. 查账和报表不需要确认：
    “今日账单”“今天花了多少钱”使用 query_today_summary；
    “本月账单简版”“这个月支出了多少”使用 query_month_summary；
    “最近5笔”“最近10笔”使用 query_recent_transactions，并写入 query.limit；
    “餐饮这个月花了多少”“交通这个月花了多少”使用 query_category_summary，并写入 query.category。
15. 报告只返回 intent 和参数，不生成报告内容：
    “记账日报”“生成今日日报”“生成昨天日报”“生成 2026-06-14 日报”“2026年6月14日的日报”
    使用 daily_report，
    指定日期时写入 query.date；
    “生成本月月报”“生成 2026-06 月报”使用 generate_monthly_report，指定月份时写入 query.month；
    “生成今年年报”“生成 2026 年报”使用 generate_yearly_report，指定年份时写入 query.year。
16. “同步财务看板”“更新财务看板”“飞书财务看板同步”已经停用，返回 unknown，不要改成 sync_bitable。
17. 重复发生的收支必须按实际发生日期展开为多笔 transactions，不能只记一笔汇总：
    “这一周每天收到公司30元餐补”从本周一展开到当前日期，每天一笔收入；
    “上周工作日每天地铁4元”只展开上周周一至周五；
    “7月1日到7月5日每天午饭20元”展开5笔。不要生成当前日期之后的流水。
18. 如果重复周期、发生频率、每次金额或收支方向不明确，使用 ask_clarification；
    单次最多展开100笔，不要臆造缺失条件。餐补、交通补贴、住房补贴归类为补贴收入。
19. conversation_context.task_mode=bookkeeping_only 时，当前入口只用于新增记账：
    有完整金额和事项时优先 create_transactions；信息不足时 ask_clarification；
    不要返回 chat、查询、同步、修改或删除意图。简短口语也按记账语句理解，但不得臆造缺失金额。
20. conversation_context.candidate_transactions 只在 Flash 结果不可靠后提供给 Pro；
    它是由确定性规则生成的有限候选。请结合用户原文逐笔验证、修正并返回完整 JSON，
    不得省略实际发生日期，也不得添加原文和候选都不支持的交易。
21. conversation_context.repair_required=true 时，上一轮 Pro 草稿未通过 retry_reason 指定的结构校验；
    必须针对该原因修复并返回完整 JSON。previous_attempt 只提供上一轮意图、置信度和笔数，
    不代表正确结果；最终 transactions 必须同时满足原文、候选和日期约束。
""".strip()


def _bookkeeping_system_prompt(base_date):
    categories = "、".join(CATEGORIES)
    return f"""
你是个人记账工具的结构化解析器。当前入口只用于新增收入或支出草稿。
你不能写数据库，只能返回一个 JSON 对象；不要 Markdown、代码块、解释或 JSON 之外的文字。

当前日期：{base_date}
category 只能是：{categories}
intent 只能是 create_transactions 或 ask_clarification。

固定 JSON：
{{
  "intent": "create_transactions",
  "confidence": 0.95,
  "reply": "",
  "clarification_question": "",
  "transactions": [{{
    "date": "YYYY-MM-DD",
    "type": "支出",
    "category": "其他",
    "amount": 0,
    "description": "",
    "tags": [],
    "is_need": false,
    "is_fixed": false
  }}],
  "requires_confirmation": true
}}

规则：
1. 只要原文包含明确事项和大于0的金额，就生成 create_transactions，不要重复追问金额。
2. 日期缺失默认 {base_date}；不得生成 {base_date} 之后的流水。
3. 收到、工资、奖金、补贴、报销、退款等通常为收入；购买、支付、花费等通常为支出。
4. 多个事项分别生成多笔，不要合并遗漏。
5. 重复发生的收支按已经发生的日期逐笔展开，最多100笔。例如本周每天、上周工作日、明确日期区间。
6. 金额、频率、周期或收支方向确实缺失时才返回 ask_clarification，并提出一个具体问题。
7. conversation_context.candidate_transactions 是确定性规则提供的候选，只用于校验和修正；
   与原文一致时完整保留，冲突时以原文为准，不得添加两者都不支持的事实。
8. tags 最多3个，只写原文能证明的消费场景、收入来源或明确项目；不要写星期、金额档位或分类名。
9. is_need、is_fixed 只能按原文和常识谨慎判断；不确定时返回 false。
10. 信息充分时 confidence 应不低于0.85；不要因为口语简短而返回 unknown 或 chat。
11. conversation_context.repair_required=true 时，上一轮草稿未通过 retry_reason 指定的校验；
    必须补全遗漏、修正日期或笔数后重新返回完整 JSON，不要重复原错误。
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

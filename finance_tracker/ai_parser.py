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
    from .ledger import CATEGORIES, parse_entry_text
except ImportError:
    from config import PROJECT_ROOT, load_env_file
    from ledger import CATEGORIES, parse_entry_text


LOGGER = logging.getLogger("finance_tracker.ai_parser")
INTENTS = {
    "create_transactions",
    "query_today_summary",
    "query_month_summary",
    "query_recent_transactions",
    "generate_report",
    "sync_bitable",
    "delete_last_transaction",
    "delete_transaction_by_id",
    "update_last_transaction",
    "update_transaction_by_id",
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
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip(),
        "timeout": max(
            1, int(os.getenv("AI_PARSER_TIMEOUT_SECONDS", "15") or "15")
        ),
    }


def parse_action(text, default_date=None, client=None, config=None):
    config = config or get_ai_parser_config()
    text = str(text or "").strip()
    base_date = _as_date(default_date or datetime.date.today()).isoformat()
    if not text:
        return _unknown("empty")

    if not config["enabled"] or not config["api_key"]:
        return _local_action(text, base_date, "disabled_or_unconfigured")

    try:
        ai_client = client or _build_client(config)
        response = ai_client.chat.completions.create(
            model=config["model"],
            timeout=config["timeout"],
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _system_prompt(base_date)},
                {"role": "user", "content": text},
            ],
        )
        raw = response.choices[0].message.content
        action = validate_action(json.loads(raw), base_date)
        _audit(text, action, "ai")
        if action["confidence"] < 0.75:
            return _local_action(text, base_date, "low_confidence")
        action["parser"] = "ai"
        action["need_confirmation"] = bool(
            action["intent"] in MUTATING_INTENTS
            and config["require_confirmation"]
        )
        return action
    except Exception as exc:
        LOGGER.warning("AI parser failed: error_type=%s", type(exc).__name__)
        if config["fallback_to_local"]:
            return _local_action(text, base_date, "ai_error")
        return _unknown("ai_error")


def validate_action(value, default_date=None):
    if not isinstance(value, dict):
        raise ValueError("AI response must be an object.")
    intent = str(value.get("intent") or "unknown")
    if intent not in INTENTS:
        raise ValueError("Unsupported intent.")
    try:
        confidence = float(value.get("confidence", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid confidence.") from exc
    if not 0 <= confidence <= 1:
        raise ValueError("Confidence must be between 0 and 1.")

    transactions = value.get("transactions") or []
    if not isinstance(transactions, list):
        raise ValueError("transactions must be a list.")
    clean_transactions = [
        _validate_transaction(item, default_date) for item in transactions
    ]
    transaction_id = value.get("transaction_id")
    if transaction_id not in (None, ""):
        transaction_id = int(transaction_id)
        if transaction_id <= 0:
            raise ValueError("transaction_id must be positive.")
    limit = int(value.get("limit") or 5)
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50.")
    updates = value.get("updates") or {}
    if not isinstance(updates, dict):
        raise ValueError("updates must be an object.")
    clean_updates = _validate_updates(updates)
    if intent == "create_transactions" and not clean_transactions:
        raise ValueError("Create intent requires transactions.")
    return {
        "intent": intent,
        "confidence": confidence,
        "transactions": clean_transactions,
        "transaction_id": transaction_id,
        "updates": clean_updates,
        "limit": limit,
        "reason": str(value.get("reason") or "")[:200],
    }


def _validate_transaction(item, default_date):
    if not isinstance(item, dict):
        raise ValueError("Each transaction must be an object.")
    amount = float(item.get("amount") or 0)
    if amount <= 0 or amount > 100000000:
        raise ValueError("Invalid transaction amount.")
    date_value = _as_date(item.get("date") or default_date).isoformat()
    txn_type = str(item.get("type") or "支出")
    if txn_type not in {"支出", "收入"}:
        raise ValueError("Invalid transaction type.")
    category = str(item.get("category") or "其他")
    if category not in CATEGORIES:
        category = "其他"
    description = str(item.get("description") or "").strip()
    if not description:
        raise ValueError("Transaction description is required.")
    return {
        "date": date_value,
        "type": txn_type,
        "category": category,
        "amount": amount,
        "description": description[:200],
        "tags": str(item.get("tags") or "")[:200],
        "is_need": int(bool(item.get("is_need", False))),
        "is_fixed": int(bool(item.get("is_fixed", False))),
    }


def _validate_updates(updates):
    allowed = {
        "date", "type", "category", "amount", "description", "tags",
        "is_need", "is_fixed",
    }
    clean = {key: value for key, value in updates.items() if key in allowed}
    if "date" in clean:
        clean["date"] = _as_date(clean["date"]).isoformat()
    if "type" in clean and clean["type"] not in {"支出", "收入"}:
        raise ValueError("Invalid update type.")
    if "category" in clean and clean["category"] not in CATEGORIES:
        raise ValueError("Invalid update category.")
    if "amount" in clean:
        clean["amount"] = float(clean["amount"])
        if clean["amount"] <= 0:
            raise ValueError("Invalid update amount.")
    if "description" in clean:
        clean["description"] = str(clean["description"]).strip()[:200]
    return clean


def _local_action(text, base_date, reason):
    mutation = _local_mutation_action(text, reason)
    if mutation:
        _audit(text, mutation, "local")
        return mutation
    records = parse_entry_text(text, base_date)
    if records:
        action = {
            "intent": "create_transactions",
            "confidence": 0.8,
            "transactions": records,
            "transaction_id": None,
            "updates": {},
            "limit": 5,
            "reason": reason,
            "parser": "local",
            "need_confirmation": True,
        }
    else:
        action = _unknown(reason)
    _audit(text, action, "local")
    return action


def _local_mutation_action(text, reason):
    delete_last = re.fullmatch(r"(?:撤销|删除)\s*(?:上一笔|刚才那笔)", text)
    if delete_last:
        return _mutation_action("delete_last_transaction", reason)
    delete_by_id = re.fullmatch(
        r"(?:撤销|删除)\s*(?:ID|id|第)?\s*(\d+)\s*(?:笔)?",
        text,
    )
    if delete_by_id:
        action = _mutation_action("delete_transaction_by_id", reason)
        action["transaction_id"] = int(delete_by_id.group(1))
        return action

    target_match = re.search(
        r"(?:ID|id)\s*(\d+)|上一笔|刚才那笔",
        text,
    )
    if not target_match or not any(word in text for word in ("改", "修改", "更正")):
        return None
    updates = {}
    amount_match = re.search(r"金额(?:改成|改为|为|是)?\s*(\d+(?:\.\d+)?)", text)
    if amount_match:
        updates["amount"] = float(amount_match.group(1))
    category_match = re.search(r"分类(?:改成|改为|为|是)?\s*([\u4e00-\u9fff]{2,4})", text)
    if category_match and category_match.group(1) in CATEGORIES:
        updates["category"] = category_match.group(1)
    description_match = re.search(
        r"(?:说明|备注)(?:改成|改为|为|是)?\s*([^，,。]+)",
        text,
    )
    if description_match:
        updates["description"] = description_match.group(1).strip()[:200]
    if not updates:
        return None
    transaction_id = int(target_match.group(1)) if target_match.group(1) else None
    intent = (
        "update_transaction_by_id"
        if transaction_id is not None
        else "update_last_transaction"
    )
    action = _mutation_action(intent, reason)
    action["transaction_id"] = transaction_id
    action["updates"] = updates
    return action


def _mutation_action(intent, reason):
    return {
        "intent": intent,
        "confidence": 0.9,
        "transactions": [],
        "transaction_id": None,
        "updates": {},
        "limit": 5,
        "reason": reason,
        "parser": "local",
        "need_confirmation": True,
    }


def _unknown(reason):
    return {
        "intent": "unknown",
        "confidence": 0.0,
        "transactions": [],
        "transaction_id": None,
        "updates": {},
        "limit": 5,
        "reason": reason,
        "parser": "none",
        "need_confirmation": False,
    }


def _build_client(config):
    if OpenAI is None:
        raise RuntimeError("openai package is not installed.")
    return OpenAI(api_key=config["api_key"], base_url=config["base_url"])


def _audit(text, action, parser):
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    LOGGER.info(
        "intent_parse text_hash=%s text_length=%d parser=%s intent=%s confidence=%.2f",
        digest,
        len(text),
        parser,
        action.get("intent", "unknown"),
        float(action.get("confidence", 0)),
    )


def _system_prompt(base_date):
    categories = "、".join(CATEGORIES)
    intents = ", ".join(sorted(INTENTS))
    return (
        "你是财务记账意图解析器，只返回单个 JSON 对象，不要 Markdown。"
        f"当前日期是 {base_date}。intent 只能是：{intents}。"
        "字段固定为 intent, confidence, transactions, transaction_id, updates, limit, reason。"
        "transactions 每项包含 date,type,category,amount,description,tags,is_need,is_fixed。"
        f"category 只能是：{categories}。金额必须为正数，日期为 YYYY-MM-DD。"
        "修改语义放入 updates；按 ID 操作时提供正整数 transaction_id。"
        "无法可靠理解时 intent=unknown 且 confidence 低于 0.75。"
    )


def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()

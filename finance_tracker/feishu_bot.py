import json
import logging
import sys
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTriggerResponse,
)

MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

try:
    from .bitable_sync import sync_pending_transactions
    from .feishu_client import FeishuClient
    from .feishu_commands import result_card, route_command
    from .feishu_config import get_feishu_config, get_feishu_config_status
    from .ledger import connect, init_db
    from .transaction_service import resolve_action
    from .config import EnvFileValidationError
except ImportError:
    from bitable_sync import sync_pending_transactions
    from feishu_client import FeishuClient
    from feishu_commands import result_card, route_command
    from feishu_config import get_feishu_config, get_feishu_config_status
    from ledger import connect, init_db
    from transaction_service import resolve_action
    from config import EnvFileValidationError


LOGGER = logging.getLogger("finance_tracker.feishu")


def configure_logging(level):
    log_dir = MODULE_DIR.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    LOGGER.setLevel(getattr(logging, level, logging.INFO))
    LOGGER.propagate = False
    handler = logging.FileHandler(log_dir / "feishu_bot.audit.log", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)


def handle_message_event(data, api_client=None, config=None):
    config = config or get_feishu_config()
    event = data.event
    message = event.message
    sender = event.sender
    message_id = str(message.message_id or "")
    event_id = str(getattr(data.header, "event_id", "") or message_id)
    chat_id = str(message.chat_id or "")
    chat_type = str(message.chat_type or "")
    sender_open_id = str(getattr(sender.sender_id, "open_id", "") or "")

    if config.bootstrap_mode:
        LOGGER.warning(
            "SECURITY WARNING: bootstrap mode is enabled; message ignored. "
            "allowed_open_ids_count=%d",
            len(config.allowed_open_ids),
        )
        return None

    if sender_open_id not in config.allowed_open_ids:
        LOGGER.warning("Rejected message from an unapproved Feishu user.")
        return None
    if config.allowed_chat_ids and chat_id not in config.allowed_chat_ids:
        LOGGER.warning("Rejected message from an unapproved Feishu chat.")
        return None
    if chat_type == "group" and not (message.mentions or []):
        return None

    duplicate = _begin_event(event_id, message_id, sender_open_id)
    if duplicate:
        return duplicate

    text = _extract_text(message.content)
    client = api_client or FeishuClient(config=config)
    try:
        result = route_command(
            text,
            context={
                "message_id": message_id,
                "event_id": event_id,
                "sender_open_id": sender_open_id,
                "chat_id": chat_id,
            },
            sync_callback=sync_pending_transactions,
        )
        response = (
            client.send_card(chat_id, result["card"])
            if result.get("card")
            else client.send_text(chat_id, result["text"])
        )
        payload = {"command": result.get("action"), "reply": response}
        _finish_event(event_id, "success", payload)
        LOGGER.info("Processed Feishu message event_id=%s action=%s", event_id, result.get("action"))
        return payload
    except Exception as exc:
        _finish_event(event_id, "failed", {"error": type(exc).__name__})
        LOGGER.exception("Feishu message processing failed for event_id=%s", event_id)
        try:
            client.send_text(chat_id, "处理失败，请稍后重试。")
        except Exception:
            LOGGER.exception("Failed to send Feishu error response.")
        return None


def handle_card_action(data, config=None):
    config = config or get_feishu_config()
    event = getattr(data, "event", None)
    operator = getattr(event, "operator", None)
    context = getattr(event, "context", None)
    action = getattr(event, "action", None)
    sender_open_id = str(getattr(operator, "open_id", "") or "")
    chat_id = str(getattr(context, "open_chat_id", "") or "")
    value = getattr(action, "value", None) or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}

    if sender_open_id not in config.allowed_open_ids:
        LOGGER.warning("Rejected card action from an unapproved Feishu user.")
        return _card_response(False, "你无权处理这个操作。")
    if config.allowed_chat_ids and chat_id not in config.allowed_chat_ids:
        LOGGER.warning("Rejected card action from an unapproved Feishu chat.")
        return _card_response(False, "这个会话未获授权。")

    result = resolve_action(
        value.get("action_id"),
        value.get("operation"),
        sender_open_id,
        chat_id,
    )
    LOGGER.info(
        "Processed Feishu card action status=%s success=%s",
        result.get("status"),
        result.get("success"),
    )
    return P2CardActionTriggerResponse(
        {
            "toast": {
                "type": "success" if result.get("success") else "warning",
                "content": result.get("message", "已处理。"),
            },
            "card": {"type": "raw", "data": result_card(result)},
        }
    )


def _card_response(success, message):
    result = {"success": success, "message": message}
    return P2CardActionTriggerResponse(
        {
            "toast": {"type": "success" if success else "warning", "content": message},
            "card": {"type": "raw", "data": result_card(result)},
        }
    )


def _extract_text(content):
    try:
        payload = json.loads(content or "{}")
        text = str(payload.get("text") or "")
    except (TypeError, json.JSONDecodeError):
        text = str(content or "")
    # Feishu replaces @ mentions with placeholders such as @_user_1.
    words = [word for word in text.split() if not word.startswith("@_")]
    return " ".join(words).strip()


def _begin_event(event_id, message_id, sender_open_id):
    init_db()
    with connect() as conn:
        existing = conn.execute(
            """
            SELECT event_id, status, response_json FROM processed_events
            WHERE event_id = ? OR message_id = ?
            ORDER BY processed_at DESC LIMIT 1
            """,
            (event_id, message_id),
        ).fetchone()
        if existing and existing[1] in {"processing", "success"}:
            return json.loads(existing[2]) if existing[2] else {"duplicate": True}
        if existing:
            conn.execute("DELETE FROM processed_events WHERE event_id = ?", (existing[0],))
        prior_transaction = conn.execute(
            "SELECT transaction_uid FROM transactions WHERE source_message_id = ? LIMIT 1",
            (message_id,),
        ).fetchone()
        if prior_transaction:
            payload = {"duplicate": True, "transaction_uid": prior_transaction[0]}
            conn.execute(
                """
                INSERT INTO processed_events
                    (event_id, message_id, sender_open_id, status, processed_at, response_json)
                VALUES (?, ?, ?, 'success', CURRENT_TIMESTAMP, ?)
                """,
                (event_id, message_id, sender_open_id, json.dumps(payload)),
            )
            return payload
        conn.execute(
            """
            INSERT INTO processed_events
                (event_id, message_id, sender_open_id, status, processed_at)
            VALUES (?, ?, ?, 'processing', CURRENT_TIMESTAMP)
            """,
            (event_id, message_id, sender_open_id),
        )
    return None


def _finish_event(event_id, status, response):
    with connect() as conn:
        conn.execute(
            """
            UPDATE processed_events
            SET status = ?, processed_at = CURRENT_TIMESTAMP, response_json = ?
            WHERE event_id = ?
            """,
            (status, json.dumps(response, ensure_ascii=False), event_id),
        )


def main():
    try:
        config = get_feishu_config()
    except EnvFileValidationError as exc:
        raise SystemExit(str(exc))
    configure_logging(config.log_level)
    status = get_feishu_config_status(config=config)
    LOGGER.info(
        "Feishu configuration loaded: allowed_open_ids_count=%d allowed_chat_ids_count=%d bootstrap_mode=%s",
        status["allowed_user_count"],
        status["allowed_chat_count"],
        status["bootstrap_mode"],
    )
    if not config.bot_enabled:
        LOGGER.info("Feishu bot is disabled.")
        return
    if status["validation_errors"]:
        raise SystemExit("; ".join(status["validation_errors"]))
    if not status["bot_ready"]:
        raise SystemExit(f"飞书配置缺失：{', '.join(status['missing'])}")
    if not config.bootstrap_mode and not config.allowed_open_ids:
        raise SystemExit("FEISHU_ALLOWED_OPEN_IDS 为空。请先使用 Bootstrap 模式获取 open_id。")

    handler = (
        lark.EventDispatcherHandler.builder(config.encrypt_key, config.verification_token)
        .register_p2_im_message_receive_v1(handle_message_event)
        .register_p2_card_action_trigger(handle_card_action)
        .build()
    )
    client = lark.ws.Client(
        config.app_id,
        config.app_secret,
        log_level=(
            lark.LogLevel.ERROR
            if config.log_level in {"ERROR", "CRITICAL"}
            else lark.LogLevel.WARNING
        ),
        event_handler=handler,
    )
    LOGGER.info("Starting Feishu long-connection bot.")
    client.start()


if __name__ == "__main__":
    main()

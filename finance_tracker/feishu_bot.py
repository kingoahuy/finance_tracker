import argparse
import json
import logging
import re
import sys
import tempfile
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
    from .bitable_sync import get_sync_dashboard, sync_pending_transactions
    from .feishu_client import FeishuClient
    from .feishu_commands import result_card, route_command
    from .feishu_config import get_feishu_config, get_feishu_config_status
    from .feishu_menu_dispatcher import MENU_EVENT_TYPE, handle_menu_event
    from .feishu_report import build_daily_report_card
    from .ledger import connect, init_db
    from .transaction_service import (
        cleanup_expired_pending_actions,
        resolve_action,
    )
    from .config import EnvFileValidationError
except ImportError:
    from bitable_sync import get_sync_dashboard, sync_pending_transactions
    from feishu_client import FeishuClient
    from feishu_commands import result_card, route_command
    from feishu_config import get_feishu_config, get_feishu_config_status
    from feishu_menu_dispatcher import MENU_EVENT_TYPE, handle_menu_event
    from feishu_report import build_daily_report_card
    from ledger import connect, init_db
    from transaction_service import (
        cleanup_expired_pending_actions,
        resolve_action,
    )
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

    cleanup_expired_pending_actions()
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
            sync_dashboard_callback=get_sync_dashboard,
        )
        payload, delivered = _send_result(client, chat_id, result)
        _finish_event(
            event_id,
            "success" if delivered else "failed",
            payload,
        )
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


def _send_result(client, chat_id, result):
    card_response = None
    fallback_text_response = None
    if result.get("card"):
        card_response = _safe_api_response(
            client.send_card(chat_id, result["card"])
        )
        if not card_response.get("success"):
            LOGGER.warning(
                "Feishu card send failed code=%s message=%s log_id=%s",
                card_response.get("code", -1),
                _safe_log_message(card_response.get("message")),
                card_response.get("log_id", ""),
            )
            fallback_text_response = _safe_api_response(
                client.send_text(
                    chat_id,
                    _fallback_confirmation_text(result),
                )
            )
        delivered = bool(
            card_response.get("success")
            or (
                fallback_text_response
                and fallback_text_response.get("success")
            )
        )
    else:
        fallback_text_response = _safe_api_response(
            client.send_text(
                chat_id,
                result.get("text") or "已处理。",
            )
        )
        delivered = bool(fallback_text_response.get("success"))
    return (
        {
            "command": result.get("action"),
            "card_response": card_response,
            "fallback_text_response": fallback_text_response,
        },
        delivered,
    )


def _fallback_confirmation_text(result):
    text = str(result.get("text") or "识别到一条待确认操作。").strip()
    if result.get("pending_action_id"):
        return f"{text}\n请回复 确认 或 取消。"
    return text


def _safe_log_message(message):
    text = str(message or "")
    text = re.sub(r"https?://\S+", "[URL]", text)
    text = re.sub(r"\b(?:ou|oc)_[A-Za-z0-9]+\b", "[ID]", text)
    text = re.sub(
        r"(?i)\b(?:tenant_)?access_token\b\s*[:=]\s*\S+",
        "access_token=[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+",
        "Bearer [REDACTED]",
        text,
    )
    return text[:500]


def _safe_api_response(response):
    response = response or {}
    return {
        "success": bool(response.get("success")),
        "code": int(response.get("code", -1) or 0),
        "message": _safe_log_message(response.get("message")),
        "log_id": str(response.get("log_id") or "")[:200],
    }


def send_feishu_text(open_id, text, api_client=None, config=None):
    """Send a private Feishu text message using an open_id."""
    client = api_client or FeishuClient(config=config or get_feishu_config())
    response = client.send_text(
        str(open_id or ""),
        str(text or ""),
        receive_id_type="open_id",
    )
    return _safe_api_response(response)


def send_feishu_card(open_id, card, api_client=None, config=None):
    """Send a private Feishu card using an open_id."""
    client = api_client or FeishuClient(config=config or get_feishu_config())
    response = client.send_card(
        str(open_id or ""),
        card,
        receive_id_type="open_id",
    )
    return _safe_api_response(response)


def handle_menu_event_callback(data, api_client=None, config=None):
    """Handle application.bot.menu_v6 without entering the chat/AI route."""
    config = config or get_feishu_config()
    event_type = str(_nested_value(data, "header", "event_type") or "")
    if event_type != MENU_EVENT_TYPE:
        LOGGER.warning("Ignored unexpected Feishu menu callback event type.")
        return None

    event_key = str(_nested_value(data, "event", "event_key") or "").strip()
    sender_open_id = str(
        _nested_value(data, "event", "operator", "operator_id", "open_id")
        or ""
    )

    if config.bootstrap_mode:
        LOGGER.warning(
            "SECURITY WARNING: bootstrap mode is enabled; menu event ignored. "
            "allowed_open_ids_count=%d",
            len(config.allowed_open_ids),
        )
        return None
    if not sender_open_id or sender_open_id not in config.allowed_open_ids:
        LOGGER.warning("Rejected menu event from an unapproved Feishu user.")
        return None

    reply_text = handle_menu_event(event_key, sender_open_id)
    try:
        if event_key == "daily_report":
            response = send_feishu_card(
                sender_open_id,
                build_daily_report_card(),
                api_client=api_client,
                config=config,
            )
            if not response.get("success"):
                response = send_feishu_text(
                    sender_open_id,
                    reply_text,
                    api_client=api_client,
                    config=config,
                )
        else:
            response = send_feishu_text(
                sender_open_id,
                reply_text,
                api_client=api_client,
                config=config,
            )
    except Exception as exc:
        LOGGER.exception(
            "Feishu menu response failed event_key=%s error=%s",
            event_key or "unknown",
            type(exc).__name__,
        )
        return None

    LOGGER.info(
        "Processed Feishu menu event event_key=%s success=%s code=%s "
        "message=%s log_id=%s",
        event_key or "unknown",
        response.get("success"),
        response.get("code"),
        _safe_log_message(response.get("message")),
        response.get("log_id"),
    )
    return response


def _nested_value(value, *path):
    current = value
    for name in path:
        if isinstance(current, dict):
            current = current.get(name)
        else:
            current = getattr(current, name, None)
        if current is None:
            return None
    return current


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
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--simulate")
    args = parser.parse_args()
    if args.simulate is not None:
        _simulate(args.simulate)
        return
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
        .register_p2_application_bot_menu_v6(handle_menu_event_callback)
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


def _simulate(text):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        from . import ledger as ledger_module
    except ImportError:
        import ledger as ledger_module

    original_db = ledger_module.DB_FILE
    with tempfile.TemporaryDirectory() as temp_dir:
        ledger_module.DB_FILE = Path(temp_dir) / "simulate.db"
        try:
            ledger_module.init_db()
            result = route_command(
                text,
                context={
                    "message_id": "simulate-message",
                    "event_id": "simulate-event",
                    "sender_open_id": "simulate-user",
                    "chat_id": "simulate-chat",
                },
            )
            print(
                json.dumps(
                    {
                        "success": result.get("success"),
                        "action": result.get("action"),
                        "text": result.get("text"),
                        "fallback_text": _fallback_confirmation_text(
                            result
                        ),
                        "card": result.get("card"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        finally:
            ledger_module.DB_FILE = original_db


if __name__ == "__main__":
    main()

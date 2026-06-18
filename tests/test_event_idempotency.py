import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from finance_tracker import feishu_bot, ledger, transaction_service
from finance_tracker.feishu_config import FeishuConfig


class FakeClient:
    def __init__(self, card_success=True):
        self.messages = []
        self.card_success = card_success

    def send_text(self, chat_id, text):
        self.messages.append((chat_id, text))
        return {"success": True, "code": 0, "message": "", "log_id": "test"}

    def send_card(self, chat_id, card):
        self.messages.append((chat_id, card))
        if not self.card_success:
            return {
                "success": False,
                "code": 230099,
                "message": "Failed to create card content",
                "log_id": "log-card-failed",
            }
        return {"success": True, "code": 0, "message": "", "log_id": "test"}


def fake_event(event_id="event-1", message_id="message-1", sender_type="user"):
    message = SimpleNamespace(
        message_id=message_id,
        chat_id="chat-1",
        chat_type="p2p",
        mentions=[],
        content=json.dumps({"text": "午饭25"}, ensure_ascii=False),
    )
    sender = SimpleNamespace(
        sender_id=SimpleNamespace(open_id="open-1"),
        sender_type=sender_type,
    )
    return SimpleNamespace(
        header=SimpleNamespace(event_id=event_id),
        event=SimpleNamespace(message=message, sender=sender),
    )


class EventIdempotencyTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = ledger.DB_FILE
        ledger.DB_FILE = Path(self.temp_dir.name) / "test.db"
        ledger.init_db()
        self.config = FeishuConfig(
            app_id="app",
            app_secret="secret",
            verification_token="",
            encrypt_key="",
            allowed_open_ids=("open-1",),
            allowed_chat_ids=("chat-1",),
            bootstrap_mode=False,
            bitable_app_token="",
            bitable_table_id="",
            bot_enabled=True,
            bitable_sync_enabled=False,
            auto_sync=False,
            daily_report_enabled=False,
            daily_report_time="21:30",
            log_level="INFO",
            sync_retry_limit=5,
        )

    def tearDown(self):
        ledger.DB_FILE = self.original_db
        self.temp_dir.cleanup()

    def test_same_message_creates_one_pending_action(self):
        client = FakeClient()
        local_parser = lambda text: {
            "intent": "create_transactions",
            "confidence": 0.8,
            "transactions": [
                {
                    "date": "2026-06-13",
                    "type": "支出",
                    "category": "餐饮",
                    "amount": 25,
                    "description": "午饭",
                    "tags": "",
                    "is_need": 1,
                    "is_fixed": 0,
                }
            ],
            "transaction_id": None,
            "updates": {},
            "limit": 5,
        }
        with mock.patch("finance_tracker.feishu_commands.parse_action", local_parser):
            first = feishu_bot.handle_message_event(fake_event(), client, self.config)
            second = feishu_bot.handle_message_event(fake_event(), client, self.config)
        with ledger.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            pending_count = conn.execute("SELECT COUNT(*) FROM pending_actions").fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(pending_count, 1)
        self.assertEqual(len(client.messages), 1)
        self.assertEqual(first["command"], "create_transactions")
        self.assertEqual(second["command"], "create_transactions")

    def test_card_failure_falls_back_to_confirmation_text(self):
        client = FakeClient(card_success=False)
        local_parser = lambda text: {
            "intent": "create_transactions",
            "confidence": 0.9,
            "transactions": [
                {
                    "date": "2026-06-14",
                    "type": "支出",
                    "category": "餐饮",
                    "amount": 10.4,
                    "description": "食堂吃饭",
                    "tags": "",
                    "is_need": 1,
                    "is_fixed": 0,
                }
            ],
            "transaction_id": None,
            "updates": {},
            "limit": 5,
        }
        with mock.patch(
            "finance_tracker.feishu_commands.parse_action",
            local_parser,
        ):
            result = feishu_bot.handle_message_event(
                fake_event("event-fallback", "message-fallback"),
                client,
                self.config,
            )
        self.assertFalse(result["card_response"]["success"])
        self.assertTrue(result["fallback_text_response"]["success"])
        self.assertEqual(len(client.messages), 2)
        fallback_text = client.messages[1][1]
        self.assertIn("¥10.40", fallback_text)
        self.assertIn("餐饮", fallback_text)
        self.assertIn("请回复 确认 或 取消", fallback_text)
        with ledger.connect() as conn:
            response_json = conn.execute(
                """
                SELECT response_json FROM processed_events
                WHERE event_id = 'event-fallback'
                """
            ).fetchone()[0]
            pending_count = conn.execute(
                "SELECT COUNT(*) FROM pending_actions"
            ).fetchone()[0]
        saved = json.loads(response_json)
        self.assertIn("card_response", saved)
        self.assertIn("fallback_text_response", saved)
        self.assertEqual(pending_count, 1)

    def test_bot_or_application_message_does_not_trigger_reply(self):
        client = FakeClient()
        result = feishu_bot.handle_message_event(
            fake_event(
                event_id="event-from-bot",
                message_id="message-from-bot",
                sender_type="app",
            ),
            client,
            self.config,
        )
        self.assertIsNone(result)
        self.assertEqual(client.messages, [])
        with ledger.connect() as conn:
            processed = conn.execute(
                "SELECT COUNT(*) FROM processed_events"
            ).fetchone()[0]
        self.assertEqual(processed, 0)


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from finance_tracker import feishu_bot, ledger, transaction_service
from finance_tracker.feishu_config import FeishuConfig


class FakeClient:
    def __init__(self):
        self.messages = []

    def send_text(self, chat_id, text):
        self.messages.append((chat_id, text))
        return {"success": True, "code": 0, "message": "", "log_id": "test"}

    def send_card(self, chat_id, card):
        self.messages.append((chat_id, card))
        return {"success": True, "code": 0, "message": "", "log_id": "test"}


def fake_event(event_id="event-1", message_id="message-1"):
    message = SimpleNamespace(
        message_id=message_id,
        chat_id="chat-1",
        chat_type="p2p",
        mentions=[],
        content=json.dumps({"text": "午饭25"}, ensure_ascii=False),
    )
    sender = SimpleNamespace(sender_id=SimpleNamespace(open_id="open-1"))
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


if __name__ == "__main__":
    unittest.main()

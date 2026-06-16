import datetime
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finance_tracker import ledger, transaction_service


class PendingActionsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = ledger.DB_FILE
        ledger.DB_FILE = Path(self.temp_dir.name) / "test.db"
        ledger.init_db()

    def tearDown(self):
        ledger.DB_FILE = self.original_db
        self.temp_dir.cleanup()

    def _action(self):
        return {
            "intent": "create_transactions",
            "transactions": [
                {
                    "date": "2026-06-13",
                    "type": "支出",
                    "category": "餐饮",
                    "amount": 25,
                    "description": "午饭",
                }
            ],
            "transaction_id": None,
            "updates": {},
        }

    def test_confirm_is_idempotent(self):
        pending = transaction_service.queue_action(
            self._action(), "open-1", "chat-1", "msg-1"
        )
        with mock.patch.object(transaction_service, "_try_sync"):
            first = transaction_service.resolve_action(
                pending["action_id"], "confirm", "open-1", "chat-1"
            )
            second = transaction_service.resolve_action(
                pending["action_id"], "confirm", "open-1", "chat-1"
            )
        with ledger.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        self.assertEqual(count, 1)

    def test_wrong_user_cannot_confirm(self):
        pending = transaction_service.queue_action(
            self._action(), "open-1", "chat-1", "msg-1"
        )
        result = transaction_service.resolve_action(
            pending["action_id"], "confirm", "open-2", "chat-1"
        )
        self.assertEqual(result["status"], "forbidden")

    def test_expired_action_does_not_execute(self):
        pending = transaction_service.queue_action(
            self._action(), "open-1", "chat-1", "msg-1"
        )
        ledger.expire_pending_actions(datetime.datetime.now() + datetime.timedelta(days=1))
        result = transaction_service.resolve_action(
            pending["action_id"], "confirm", "open-1", "chat-1"
        )
        self.assertEqual(result["status"], "expired")
        self.assertEqual(
            result["message"],
            "这条待确认操作已过期，请重新发送记账内容。",
        )
        with ledger.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM transactions"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_cleanup_expired_pending_actions(self):
        pending = transaction_service.queue_action(
            self._action(), "open-1", "chat-1", "msg-1"
        )
        with ledger.connect() as conn:
            conn.execute(
                """
                UPDATE pending_actions
                SET expires_at = '2000-01-01 00:00:00'
                WHERE action_id = ?
                """,
                (pending["action_id"],),
            )
        cleaned = transaction_service.cleanup_expired_pending_actions()
        self.assertEqual(cleaned, 1)
        self.assertEqual(
            ledger.get_pending_action(pending["action_id"])["status"],
            "expired",
        )


if __name__ == "__main__":
    unittest.main()

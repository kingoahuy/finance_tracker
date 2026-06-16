import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finance_tracker import ledger, transaction_service


class FeishuDeleteUpdateTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = ledger.DB_FILE
        ledger.DB_FILE = Path(self.temp_dir.name) / "test.db"
        ledger.init_db()
        self.record = transaction_service.create_transaction(
            {
                "date": "2026-06-13",
                "type": "支出",
                "category": "餐饮",
                "amount": 25,
                "description": "午饭",
            },
            source="feishu",
            source_user_open_id="open-1",
            source_chat_id="chat-1",
            auto_sync=False,
        )

    def tearDown(self):
        ledger.DB_FILE = self.original_db
        self.temp_dir.cleanup()

    def test_other_user_cannot_delete_or_update(self):
        self.assertIsNone(
            transaction_service.soft_delete_transaction(
                "open-2", "chat-1", self.record["id"], auto_sync=False
            )
        )
        self.assertIsNone(
            transaction_service.update_owned_transaction(
                "open-2",
                {"amount": 30},
                "chat-1",
                self.record["id"],
                auto_sync=False,
            )
        )

    def test_owner_can_update_then_soft_delete(self):
        updated = transaction_service.update_owned_transaction(
            "open-1",
            {"amount": 30},
            "chat-1",
            self.record["id"],
            auto_sync=False,
        )
        self.assertEqual(updated["amount"], 30)
        deleted = transaction_service.soft_delete_transaction(
            "open-1", "chat-1", self.record["id"], auto_sync=False
        )
        self.assertEqual(deleted["status"], "deleted")
        self.assertTrue(ledger.load_transactions().empty)
        self.assertEqual(len(ledger.load_transactions(include_deleted=True)), 1)

    def test_date_change_syncs_original_detail_only(self):
        with mock.patch.object(transaction_service, "_try_sync") as sync:
            updated = transaction_service.update_owned_transaction(
                "open-1",
                {"date": "2026-07-01"},
                "chat-1",
                self.record["id"],
            )
        sync.assert_called_once_with(updated["transaction_uid"])

    def test_delete_syncs_original_detail_only(self):
        with mock.patch.object(transaction_service, "_try_sync") as sync:
            deleted = transaction_service.soft_delete_transaction(
                "open-1",
                "chat-1",
                self.record["id"],
            )
        sync.assert_called_once_with(deleted["transaction_uid"])


if __name__ == "__main__":
    unittest.main()

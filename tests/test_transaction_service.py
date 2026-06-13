import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finance_tracker import ledger, transaction_service


class TransactionServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.original_db = ledger.DB_FILE
        ledger.DB_FILE = self.db_path
        ledger.init_db()

    def tearDown(self):
        ledger.DB_FILE = self.original_db
        self.temp_dir.cleanup()

    def test_feishu_and_streamlit_sources(self):
        with mock.patch.object(transaction_service, "_try_sync"):
            feishu = transaction_service.create_transaction(
                {"date": "2026-06-13", "type": "支出", "category": "餐饮", "amount": 25, "description": "午饭"},
                source="feishu",
                source_message_id="msg-1",
            )
            streamlit = transaction_service.create_transaction(
                {"date": "2026-06-13", "type": "收入", "category": "工资", "amount": 100, "description": "工资"},
                source="streamlit",
            )
        self.assertEqual(feishu["source"], "feishu")
        self.assertEqual(feishu["source_message_id"], "msg-1")
        self.assertEqual(streamlit["source"], "streamlit")

    def test_undo_last_transaction(self):
        with mock.patch.object(transaction_service, "_try_sync"):
            created = transaction_service.create_transaction(
                {"date": "2026-06-13", "type": "支出", "category": "餐饮", "amount": 25, "description": "午饭"},
                source="feishu",
                source_user_open_id="open-1",
                source_chat_id="chat-1",
                auto_sync=False,
            )
            undone = transaction_service.soft_delete_transaction(
                "open-1", "chat-1", auto_sync=False
            )
        self.assertEqual(undone["transaction_uid"], created["transaction_uid"])
        self.assertEqual(len(transaction_service.get_recent_transactions()), 0)

    def test_month_summary(self):
        with mock.patch.object(transaction_service, "_try_sync"):
            transaction_service.create_transaction(
                {"date": "2026-06-01", "type": "收入", "category": "工资", "amount": 1000, "description": "工资"},
                auto_sync=False,
            )
            transaction_service.create_transaction(
                {"date": "2026-06-02", "type": "支出", "category": "餐饮", "amount": 100, "description": "吃饭"},
                auto_sync=False,
            )
        summary = transaction_service.get_month_summary("2026-06-13")
        self.assertEqual(summary["income"], 1000)
        self.assertEqual(summary["expense"], 100)
        self.assertEqual(summary["balance"], 900)
        self.assertEqual(summary["top_categories"][0]["category"], "餐饮")


if __name__ == "__main__":
    unittest.main()

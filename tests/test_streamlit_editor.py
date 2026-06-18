import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finance_tracker import ledger


class StreamlitEditorTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = ledger.DB_FILE
        ledger.DB_FILE = Path(self.temp_dir.name) / "test.db"
        ledger.init_db()
        self.first = ledger.add_transaction(
            {
                "date": "2026-06-13",
                "type": "支出",
                "category": "餐饮",
                "amount": 25,
                "description": "午饭",
            }
        )
        self.second = ledger.add_transaction(
            {
                "date": "2026-06-13",
                "type": "支出",
                "category": "交通",
                "amount": 4,
                "description": "地铁",
            }
        )

    def tearDown(self):
        ledger.DB_FILE = self.original_db
        self.temp_dir.cleanup()

    def test_modify_one_and_delete_one_from_snapshot(self):
        original = ledger.load_transactions()
        edited = original[original["id"] == self.first["id"]].copy()
        edited.loc[:, "amount"] = 30
        result = ledger.update_transactions_from_editor(
            edited,
            original_rowids=original["_rowid"].tolist(),
        )
        self.assertEqual(result, {"updated": 1, "created": 0, "deleted": 1})
        active = ledger.load_transactions()
        all_rows = ledger.load_transactions(include_deleted=True)
        self.assertEqual(len(active), 1)
        self.assertEqual(float(active.iloc[0]["amount"]), 30)
        self.assertEqual(float(active.iloc[0]["net_amount"]), -30)
        deleted = all_rows[all_rows["status"] == "deleted"]
        self.assertEqual(len(deleted), 1)
        self.assertEqual(deleted.iloc[0]["description"], "地铁")
        self.assertEqual(int(deleted.iloc[0]["is_active"]), 0)

    def test_unchanged_rows_are_not_enqueued_again(self):
        original = ledger.load_transactions()
        with ledger.connect() as conn:
            before = conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]
        result = ledger.update_transactions_from_editor(
            original.copy(),
            original_rowids=original["_rowid"].tolist(),
        )
        with ledger.connect() as conn:
            after = conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]
        self.assertEqual(result, {"updated": 0, "created": 0, "deleted": 0})
        self.assertEqual(after, before)

    def test_snapshot_does_not_delete_records_loaded_elsewhere(self):
        original = ledger.load_transactions()
        first_only = original[original["id"] == self.first["id"]].copy()
        result = ledger.update_transactions_from_editor(
            first_only,
            original_rowids=first_only["_rowid"].tolist(),
        )
        self.assertEqual(result["deleted"], 0)
        self.assertEqual(len(ledger.load_transactions()), 2)


if __name__ == "__main__":
    unittest.main()

import datetime
import tempfile
import unittest
from pathlib import Path

from finance_tracker import data_cleanup, ledger


class DataCleanupTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = ledger.DB_FILE
        ledger.DB_FILE = Path(self.temp_dir.name) / "cleanup.db"
        ledger.init_db()

    def tearDown(self):
        ledger.DB_FILE = self.original_db
        data_cleanup._LAST_CLEANUP_DATE = None
        self.temp_dir.cleanup()

    def _add(self, description):
        return ledger.add_transaction(
            {
                "date": "2026-07-18",
                "type": "支出",
                "category": "其他",
                "amount": 10,
                "description": description,
            }
        )

    def test_cleanup_deletes_only_provably_invalid_and_expired_rows(self):
        valid = self._add("valid")
        invalid = self._add("invalid")
        expired = self._add("expired")
        recent = self._add("recent")
        with ledger.connect() as conn:
            conn.execute(
                "UPDATE transactions SET transaction_uid = 'test_invalid', amount = 0 "
                "WHERE transaction_uid = ?",
                (invalid["transaction_uid"],),
            )
            conn.execute(
                "UPDATE sync_outbox SET transaction_uid = 'test_invalid' "
                "WHERE transaction_uid = ?",
                (invalid["transaction_uid"],),
            )
            conn.execute(
                """
                UPDATE transactions
                SET status = 'deleted', deleted_at = '2026-05-01 00:00:00',
                    sync_status = 'synced'
                WHERE transaction_uid = ?
                """,
                (expired["transaction_uid"],),
            )
            conn.execute(
                """
                UPDATE transactions
                SET status = 'deleted', deleted_at = '2026-07-17 00:00:00',
                    sync_status = 'synced'
                WHERE transaction_uid = ?
                """,
                (recent["transaction_uid"],),
            )

        dry_run = data_cleanup.cleanup_invalid_transactions(
            apply=False,
            now=datetime.datetime(2026, 7, 18, 4, 0),
            retention_days=30,
            include_remote_tests=False,
        )
        self.assertEqual(dry_run["planned_delete_count"], 2)
        self.assertEqual(len(ledger.load_transactions(include_deleted=True)), 4)

        applied = data_cleanup.cleanup_invalid_transactions(
            apply=True,
            now=datetime.datetime(2026, 7, 18, 4, 0),
            retention_days=30,
            include_remote_tests=False,
        )
        self.assertTrue(applied["success"])
        self.assertEqual(applied["local_deleted_count"], 2)
        remaining = ledger.load_transactions(include_deleted=True)
        self.assertEqual(len(remaining), 2)
        self.assertIn(valid["transaction_uid"], remaining["transaction_uid"].tolist())
        self.assertIn(recent["transaction_uid"], remaining["transaction_uid"].tolist())


if __name__ == "__main__":
    unittest.main()

import datetime
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from finance_tracker import bitable_sync, ledger, transaction_service
from finance_tracker.bitable_sync import FIELD_MAP, transaction_to_bitable_fields


class BitableDeletedSyncTest(unittest.TestCase):
    def test_deleted_state_is_mapped_for_remote_update(self):
        fields = transaction_to_bitable_fields(
            {
                "transaction_uid": "uid-1",
                "id": 1,
                "date": "2026-06-13",
                "amount": 25,
                "description": "午饭",
                "status": "deleted",
                "deleted_at": "2026-06-13 12:30:00",
                "deleted_by_open_id": "open-1",
                "delete_reason": "user request",
            }
        )
        self.assertEqual(fields[FIELD_MAP["status"]], "deleted")
        self.assertEqual(fields[FIELD_MAP["delete_reason"]], "user request")
        self.assertNotEqual(
            fields[FIELD_MAP["deleted_by_open_id"]],
            "open-1",
        )
        self.assertEqual(
            len(fields[FIELD_MAP["deleted_by_open_id"]]),
            12,
        )
        self.assertEqual(
            fields[FIELD_MAP["deleted_at"]],
            int(datetime.datetime(2026, 6, 13, 12, 30).timestamp() * 1000),
        )

    def test_soft_deleted_record_is_updated_not_physically_deleted(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        original_db = ledger.DB_FILE
        self.addCleanup(setattr, ledger, "DB_FILE", original_db)
        ledger.DB_FILE = Path(temp_dir.name) / "test.db"
        ledger.init_db()
        record = transaction_service.create_transaction(
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
        transaction_service.soft_delete_transaction(
            "open-1", "chat-1", record["id"], auto_sync=False
        )

        class FakeService:
            config = SimpleNamespace(
                bitable_sync_enabled=True,
                bitable_ready=True,
            )

            def __init__(self):
                self.updated = None

            def find_record_id(self, transaction_uid):
                return "rec-1"

            def update_record(self, record_id, transaction):
                self.updated = transaction
                return {"success": True, "message": "", "record_id": record_id}

            def create_record(self, transaction):
                raise AssertionError("deleted mirrored record should be updated")

            def delete_record(self, record_id):
                raise AssertionError("remote record must not be physically deleted")

        service = FakeService()
        result = bitable_sync.sync_transaction(
            record["transaction_uid"], operation="update", service=service
        )
        self.assertTrue(result["success"])
        self.assertEqual(service.updated["status"], "deleted")


if __name__ == "__main__":
    unittest.main()

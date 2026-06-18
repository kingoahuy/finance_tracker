import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finance_tracker import ledger, transaction_service
from finance_tracker.derived_fields import DATA_VERSION, DERIVED_COLUMNS


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

    def _fetch_derived(self, transaction_uid):
        with ledger.connect() as conn:
            row = conn.execute(
                f"""
                SELECT {", ".join(DERIVED_COLUMNS)}
                FROM transactions
                WHERE transaction_uid = ?
                """,
                (transaction_uid,),
            ).fetchone()
        self.assertIsNotNone(row)
        return dict(zip(DERIVED_COLUMNS, row))

    def _assert_derived_complete(self, record):
        text_columns = {
            "date_year_month",
            "date_weekday",
            "date_quarter",
            "amount_bucket",
            "tags_text",
            "ledger_month",
            "data_version",
        }
        for column in DERIVED_COLUMNS:
            self.assertIn(column, record)
            self.assertIsNotNone(record[column], column)
            if column in text_columns:
                self.assertNotEqual(str(record[column]).strip(), "", column)

    def test_add_transaction_writes_expense_derived_fields_immediately(self):
        record = ledger.add_transaction(
            {
                "date": "2026-06-14",
                "type": "支出",
                "category": "餐饮",
                "amount": 10.4,
                "description": "早餐",
                "tags": "早餐",
            }
        )
        self._assert_derived_complete(record)
        self.assertEqual(record["date_year"], 2026)
        self.assertEqual(record["date_year_month"], "2026-06")
        self.assertEqual(record["date_month"], 6)
        self.assertEqual(record["date_day"], 14)
        self.assertEqual(record["date_quarter"], "2026Q2")
        self.assertEqual(record["ledger_month"], "2026-06")
        self.assertEqual(record["income_amount"], 0.0)
        self.assertEqual(record["expense_amount"], 10.4)
        self.assertEqual(record["net_amount"], -10.4)
        self.assertEqual(record["is_income"], 0)
        self.assertEqual(record["is_expense"], 1)
        self.assertEqual(record["is_active"], 1)
        self.assertEqual(record["data_version"], DATA_VERSION)

        persisted = self._fetch_derived(record["transaction_uid"])
        self._assert_derived_complete(persisted)
        self.assertEqual(persisted["expense_amount"], 10.4)
        self.assertEqual(persisted["net_amount"], -10.4)

    def test_add_transaction_writes_income_derived_fields_immediately(self):
        record = ledger.add_transaction(
            {
                "date": "2026-06-14",
                "type": "收入",
                "category": "工资",
                "amount": 20000,
                "description": "工资",
                "tags": "工资",
            }
        )
        self._assert_derived_complete(record)
        self.assertEqual(record["income_amount"], 20000.0)
        self.assertEqual(record["expense_amount"], 0.0)
        self.assertEqual(record["net_amount"], 20000.0)
        self.assertEqual(record["is_income"], 1)
        self.assertEqual(record["is_expense"], 0)

        persisted = self._fetch_derived(record["transaction_uid"])
        self._assert_derived_complete(persisted)
        self.assertEqual(persisted["income_amount"], 20000.0)
        self.assertEqual(persisted["net_amount"], 20000.0)

    def test_create_transactions_and_pending_confirm_write_derived_fields(self):
        with mock.patch.object(transaction_service, "_try_sync"):
            created = transaction_service.create_transactions(
                [
                    {
                        "date": "2026-06-14",
                        "type": "支出",
                        "category": "餐饮",
                        "amount": 10.4,
                        "description": "早餐",
                        "tags": "早餐",
                    }
                ],
                source="feishu",
                source_user_open_id="open-1",
                source_chat_id="chat-1",
            )[0]
        self._assert_derived_complete(created)
        self._assert_derived_complete(
            self._fetch_derived(created["transaction_uid"])
        )

        pending = transaction_service.queue_action(
            {
                "intent": "create_transactions",
                "transactions": [
                    {
                        "date": "2026-06-14",
                        "type": "收入",
                        "category": "工资",
                        "amount": 20000,
                        "description": "工资",
                        "tags": "工资",
                    }
                ],
            },
            "open-1",
            "chat-1",
            source_message_id="msg-1",
        )
        with mock.patch.object(transaction_service, "_try_sync"):
            result = transaction_service.resolve_action(
                pending["action_id"],
                "confirm",
                "open-1",
                "chat-1",
            )
        self.assertTrue(result["success"])
        confirmed = result["transactions"][0]
        self._assert_derived_complete(confirmed)
        self.assertEqual(confirmed["income_amount"], 20000.0)
        self.assertEqual(confirmed["net_amount"], 20000.0)
        self._assert_derived_complete(
            self._fetch_derived(confirmed["transaction_uid"])
        )

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

    def test_create_transaction_syncs_original_detail_only(self):
        with mock.patch.object(transaction_service, "_try_sync") as sync:
            created = transaction_service.create_transaction(
                {
                    "date": "2026-06-13",
                    "type": "支出",
                    "category": "餐饮",
                    "amount": 25,
                    "description": "午饭",
                }
            )
        sync.assert_called_once_with(created["transaction_uid"])

    def test_batch_create_syncs_each_original_detail_once(self):
        with mock.patch.object(transaction_service, "_try_sync") as sync:
            created = transaction_service.create_transactions(
                [
                    {
                        "date": "2026-06-13",
                        "type": "支出",
                        "category": "餐饮",
                        "amount": 25,
                        "description": "午饭",
                    },
                    {
                        "date": "2026-06-14",
                        "type": "支出",
                        "category": "交通",
                        "amount": 4,
                        "description": "地铁",
                    },
                ]
            )
        self.assertEqual(len(created), 2)
        self.assertEqual(sync.call_count, 2)
        sync.assert_has_calls(
            [
                mock.call(created[0]["transaction_uid"]),
                mock.call(created[1]["transaction_uid"]),
            ]
        )

    @mock.patch("finance_tracker.bitable_sync.sync_transaction")
    @mock.patch(
        "finance_tracker.bitable_sync.auto_sync_enabled",
        return_value=True,
    )
    def test_try_sync_updates_original_detail_table_only(
        self,
        _auto_sync_enabled,
        sync_transaction,
    ):
        transaction_service._try_sync("uid-1")
        sync_transaction.assert_called_once_with("uid-1")


if __name__ == "__main__":
    unittest.main()

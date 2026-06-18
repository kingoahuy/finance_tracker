import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from finance_tracker import bitable_sync, ledger, transaction_service
from finance_tracker.derived_fields import DERIVED_COLUMNS, DERIVED_FIELD_LABELS


def fake_config(**overrides):
    values = {
        "app_id": "app",
        "app_secret": "secret",
        "bitable_app_token": "base-token",
        "bitable_table_id": "tbl-test",
        "bitable_sync_enabled": True,
        "auto_sync": True,
        "bot_ready": True,
        "bitable_ready": True,
        "sync_retry_limit": 5,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeFieldService:
    def __init__(self, result):
        self.config = fake_config()
        self.result = result

    def list_fields(self):
        return self.result


class FakeRemoteService:
    def __init__(self, records):
        self.config = fake_config()
        self.records = records
        self.deleted = []

    def list_records(self):
        return {
            "success": True,
            "code": 0,
            "message": "success",
            "log_id": "log-list",
            "records": self.records,
        }

    def delete_records(self, record_ids):
        self.deleted.extend(record_ids)
        return {
            "success": True,
            "code": 0,
            "message": "success",
            "log_id": "log-delete",
            "deleted_record_ids": list(record_ids),
        }


class FakeTableService:
    def __init__(self, tables=None):
        self.config = fake_config(bitable_table_id="tbl-original")
        self.tables = tables or [
            {"name": "原始数据表", "table_id": "tbl-original"},
            {"name": "月度汇总表", "table_id": "tbl-summary"},
        ]
        self.deleted = []
        self.created_fields = []

    def list_tables(self):
        return {
            "success": True,
            "code": 0,
            "message": "success",
            "log_id": "log-tables",
            "tables": list(self.tables),
        }

    def list_fields(self, table_id=None):
        return {
            "success": True,
            "code": 0,
            "message": "success",
            "log_id": f"log-fields-{table_id}",
            "fields": [
                {"field_name": "字段1"},
                {"field_name": "字段2"},
            ],
        }

    def list_records(self, table_id=None):
        records = (
            [remote_record("rec-1", "uid-1", 1)]
            if table_id == "tbl-original"
            else []
        )
        return {
            "success": True,
            "code": 0,
            "message": "success",
            "log_id": f"log-records-{table_id}",
            "records": records,
        }

    def delete_table(self, table_id):
        self.deleted.append(table_id)
        return {
            "success": True,
            "code": 0,
            "message": "success",
            "log_id": f"log-delete-{table_id}",
            "table_id": table_id,
        }

    def create_field(self, field_name, field_type, table_id=None):
        self.created_fields.append(
            {
                "field_name": field_name,
                "field_type": field_type,
                "table_id": table_id,
            }
        )
        return {
            "success": True,
            "code": 0,
            "message": "success",
            "log_id": f"log-create-{field_name}",
            "field_name": field_name,
        }


def remote_record(record_id, uid="", local_id=0, description="", created=0):
    return {
        "record_id": record_id,
        "created_time": created,
        "last_modified_time": created,
        "fields": {
            bitable_sync.FIELD_MAP["transaction_uid"]: uid,
            bitable_sync.FIELD_MAP["id"]: local_id,
            bitable_sync.FIELD_MAP["description"]: description,
        },
    }


def required_field_defs(names=None):
    names = list(names or bitable_sync.REQUIRED_FIELDS)
    base_types = {
        bitable_sync.FIELD_MAP["transaction_uid"]: (1, "Text"),
        bitable_sync.FIELD_MAP["id"]: (2, "Number"),
        bitable_sync.FIELD_MAP["date"]: (5, "DateTime"),
        bitable_sync.FIELD_MAP["type"]: (3, "SingleSelect"),
        bitable_sync.FIELD_MAP["category"]: (3, "SingleSelect"),
        bitable_sync.FIELD_MAP["amount"]: (2, "Number"),
        bitable_sync.FIELD_MAP["description"]: (1, "Text"),
        bitable_sync.FIELD_MAP["tags"]: (4, "MultiSelect"),
        bitable_sync.FIELD_MAP["is_need"]: (7, "Checkbox"),
        bitable_sync.FIELD_MAP["is_fixed"]: (7, "Checkbox"),
        bitable_sync.FIELD_MAP["source"]: (3, "SingleSelect"),
        bitable_sync.FIELD_MAP["source_message_id"]: (1, "Text"),
        bitable_sync.FIELD_MAP["created_at"]: (5, "DateTime"),
        bitable_sync.FIELD_MAP["updated_at"]: (5, "DateTime"),
        bitable_sync.FIELD_MAP["status"]: (3, "SingleSelect"),
        bitable_sync.FIELD_MAP["deleted_at"]: (5, "DateTime"),
        bitable_sync.FIELD_MAP["deleted_by_open_id"]: (1, "Text"),
        bitable_sync.FIELD_MAP["delete_reason"]: (1, "Text"),
    }
    derived_ui = {1: "Text", 2: "Number", 7: "Checkbox"}
    for label, field_type in bitable_sync.DERIVED_BITABLE_TYPES.items():
        base_types[label] = (field_type, derived_ui[field_type])
    return [
        {
            "field_name": name,
            "type": base_types.get(name, (1, "Text"))[0],
            "ui_type": base_types.get(name, (1, "Text"))[1],
        }
        for name in names
    ]


class BitableDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = ledger.DB_FILE
        ledger.DB_FILE = Path(self.temp_dir.name) / "test.db"
        ledger.init_db()

    def tearDown(self):
        ledger.DB_FILE = self.original_db
        self.temp_dir.cleanup()

    def test_validate_fields_reports_exact_missing_names(self):
        present = required_field_defs(bitable_sync.REQUIRED_FIELDS[:-2])
        service = FakeFieldService(
            {
                "success": True,
                "code": 0,
                "message": "",
                "log_id": "log-ok",
                "fields": present,
            }
        )
        result = bitable_sync.validate_fields(service)
        self.assertFalse(result["success"])
        self.assertEqual(
            result["missing_fields"],
            list(bitable_sync.REQUIRED_FIELDS[-2:]),
        )
        self.assertIn(bitable_sync.REQUIRED_FIELDS[-1], result["message"])

    def test_validate_fields_requires_tags_to_be_multi_select(self):
        fields = required_field_defs()
        for field in fields:
            if field["field_name"] == bitable_sync.FIELD_MAP["tags"]:
                field["type"] = 1
                field["ui_type"] = "Text"
        service = FakeFieldService(
            {
                "success": True,
                "code": 0,
                "message": "",
                "log_id": "log-field-type",
                "fields": fields,
            }
        )
        result = bitable_sync.validate_fields(service)
        self.assertFalse(result["success"])
        self.assertIn("必须设置为多选", result["message"])
        self.assertEqual(
            result["invalid_field_types"][0]["field"],
            bitable_sync.FIELD_MAP["tags"],
        )

    def test_connection_returns_api_diagnostics(self):
        service = FakeFieldService(
            {
                "success": False,
                "code": 99991672,
                "message": "Access denied",
                "log_id": "log-denied",
            }
        )
        result = bitable_sync.test_bitable_connection(service)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], 99991672)
        self.assertEqual(result["message"], "Access denied")
        self.assertEqual(result["log_id"], "log-denied")

    def test_permission_message_redacts_url_and_identifiers(self):
        message = (
            "Access denied https://open.feishu.cn/app/cli_secret/auth "
            "sender=ou_private chat=oc_private "
            "access_token=token-private Bearer bearer-private"
        )
        safe = bitable_sync._sanitize_api_message(message)
        self.assertNotIn("https://", safe)
        self.assertNotIn("cli_secret", safe)
        self.assertNotIn("ou_private", safe)
        self.assertNotIn("oc_private", safe)
        self.assertNotIn("token-private", safe)
        self.assertNotIn("bearer-private", safe)
        self.assertIn("Access denied", safe)

    def test_search_failure_is_not_treated_as_missing_record(self):
        record = transaction_service.create_transaction(
            {
                "date": "2026-06-14",
                "type": "支出",
                "category": "餐饮",
                "amount": 25,
                "description": "午饭",
            },
            auto_sync=False,
        )

        class SearchFailService:
            config = fake_config()

            def find_record_id(self, transaction_uid):
                raise bitable_sync.BitableSyncError(
                    {
                        "code": 99991672,
                        "message": "Access denied",
                        "log_id": "log-search",
                    }
                )

            def create_record(self, transaction):
                raise AssertionError("search failure must not create a record")

        result = bitable_sync.sync_transaction(
            record["transaction_uid"],
            service=SearchFailService(),
        )
        self.assertFalse(result["success"])
        with ledger.connect() as conn:
            status, error = conn.execute(
                """
                SELECT sync_status, sync_error
                FROM transactions WHERE transaction_uid = ?
                """,
                (record["transaction_uid"],),
            ).fetchone()
        self.assertEqual(status, "failed")
        self.assertIn("code=99991672", error)
        self.assertIn("log_id=log-search", error)

    def test_duplicate_search_stops_before_create(self):
        record = transaction_service.create_transaction(
            {
                "date": "2026-06-14",
                "type": "支出",
                "category": "餐饮",
                "amount": 25,
                "description": "private",
            },
            auto_sync=False,
        )

        class DuplicateService:
            config = fake_config()

            def search_record(self, transaction_uid):
                return {
                    "success": True,
                    "code": 0,
                    "message": "success",
                    "log_id": "log-search",
                    "record_id": None,
                    "match_count": 2,
                }

            def create_record(self, transaction):
                raise AssertionError("duplicate UID must not create")

        result = bitable_sync.sync_transaction(
            record["transaction_uid"],
            service=DuplicateService(),
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], -2)
        self.assertIn("--dedupe-remote --dry-run", result["message"])

    def test_pending_preflight_failure_is_clear_and_persisted(self):
        record = transaction_service.create_transaction(
            {
                "date": "2026-06-14",
                "type": "支出",
                "category": "交通",
                "amount": 4,
                "description": "地铁",
            },
            auto_sync=False,
        )
        service = FakeFieldService(
            {
                "success": False,
                "code": 99991672,
                "message": "Access denied",
                "log_id": "log-preflight",
            }
        )
        result = bitable_sync.sync_pending_transactions(
            limit=10,
            service=service,
        )
        self.assertFalse(result["success"])
        self.assertIn("Access denied", result["message"])
        with ledger.connect() as conn:
            error = conn.execute(
                """
                SELECT sync_error FROM transactions
                WHERE transaction_uid = ?
                """,
                (record["transaction_uid"],),
            ).fetchone()[0]
        self.assertIn("log_id=log-preflight", error)

    def test_batch_progress_every_ten_and_failure_immediately(self):
        rows = [(f"uid-{index}", "update") for index in range(1, 13)]
        events = []

        def fake_sync(uid, operation=None, service=None):
            if uid == "uid-3":
                return {
                    "success": False,
                    "code": 1255040,
                    "message": "request timeout",
                    "log_id": "log-timeout",
                }
            return {
                "success": True,
                "code": 0,
                "message": "",
                "log_id": "",
            }

        with mock.patch.object(
            bitable_sync,
            "sync_transaction",
            side_effect=fake_sync,
        ):
            results = bitable_sync._sync_rows(
                rows,
                service=object(),
                progress_callback=events.append,
            )
        self.assertEqual(len(results), 12)
        self.assertEqual(events[0]["event"], "error")
        self.assertEqual(events[0]["processed"], 3)
        progress = [
            event["processed"]
            for event in events
            if event["event"] == "progress"
        ]
        self.assertEqual(progress, [10, 12])
        self.assertEqual(events[0]["transaction_uid_prefix"], "uid-3")
        self.assertIn("local_id", events[0])

    def test_reset_failed_sync_only_resets_failed_rows(self):
        failed = transaction_service.create_transaction(
            {
                "date": "2026-06-14",
                "type": "支出",
                "category": "交通",
                "amount": 4,
                "description": "private",
            },
            auto_sync=False,
        )
        pending = transaction_service.create_transaction(
            {
                "date": "2026-06-14",
                "type": "支出",
                "category": "餐饮",
                "amount": 25,
                "description": "private",
            },
            auto_sync=False,
        )
        with ledger.connect() as conn:
            conn.execute(
                """
                UPDATE transactions
                SET sync_status = 'failed', sync_error = 'old error'
                WHERE transaction_uid = ?
                """,
                (failed["transaction_uid"],),
            )
            conn.execute(
                """
                UPDATE sync_outbox
                SET status = 'failed', retry_count = 5,
                    last_error = 'old error'
                WHERE transaction_uid = ?
                """,
                (failed["transaction_uid"],),
            )

        result = bitable_sync.reset_failed_sync()

        self.assertTrue(result["success"])
        self.assertEqual(result["transactions_reset"], 1)
        self.assertEqual(result["outbox_reset"], 1)
        with ledger.connect() as conn:
            failed_state = conn.execute(
                """
                SELECT sync_status, sync_error
                FROM transactions WHERE transaction_uid = ?
                """,
                (failed["transaction_uid"],),
            ).fetchone()
            outbox_state = conn.execute(
                """
                SELECT status, retry_count, last_error
                FROM sync_outbox WHERE transaction_uid = ?
                """,
                (failed["transaction_uid"],),
            ).fetchone()
            pending_state = conn.execute(
                """
                SELECT sync_status
                FROM transactions WHERE transaction_uid = ?
                """,
                (pending["transaction_uid"],),
            ).fetchone()[0]
        self.assertEqual(failed_state, ("pending", ""))
        self.assertEqual(outbox_state, ("pending", 0, ""))
        self.assertEqual(pending_state, "pending")

    def test_sync_dashboard_contains_safe_counts_and_errors(self):
        record = transaction_service.create_transaction(
            {
                "date": "2026-06-14",
                "type": "支出",
                "category": "餐饮",
                "amount": 25,
                "description": "must-not-appear",
            },
            auto_sync=False,
        )
        with ledger.connect() as conn:
            conn.execute(
                """
                UPDATE transactions
                SET sync_status = 'failed',
                    sync_error = 'code=1 access_token=private-token'
                WHERE transaction_uid = ?
                """,
                (record["transaction_uid"],),
            )
        with mock.patch.object(
            bitable_sync,
            "get_feishu_config",
            return_value=fake_config(),
        ):
            dashboard = bitable_sync.get_sync_dashboard(
                check_fields=False
            )
        self.assertEqual(dashboard["counts"]["total"], 1)
        self.assertEqual(dashboard["counts"]["failed"], 1)
        self.assertEqual(
            dashboard["recent_errors"][0]["local_id"],
            record["id"],
        )
        self.assertNotIn("must-not-appear", str(dashboard))
        self.assertNotIn("private-token", str(dashboard))

    def test_remote_audit_counts_duplicates_orphans_and_tests(self):
        local = transaction_service.create_transaction(
            {
                "date": "2026-06-14",
                "type": "支出",
                "category": "餐饮",
                "amount": 25,
                "description": "private",
            },
            auto_sync=False,
        )
        records = [
            remote_record("rec-1", local["transaction_uid"], 1),
            remote_record("rec-2", local["transaction_uid"], 1),
            remote_record("rec-orphan", "orphan-uid", 99),
            remote_record("rec-test", "test_probe", 0),
            remote_record("rec-empty", "", 0),
            remote_record(
                "rec-permission",
                "permission-probe",
                0,
                "权限测试",
            ),
        ]
        result = bitable_sync.audit_remote(
            service=FakeRemoteService(records)
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["local_total"], 1)
        self.assertEqual(result["remote_total"], 6)
        self.assertEqual(result["remote_uid_empty"], 1)
        self.assertEqual(result["remote_duplicate_uid_count"], 1)
        self.assertEqual(result["remote_duplicate_extra_records"], 1)
        self.assertEqual(result["remote_orphan_uid_count"], 3)
        self.assertEqual(result["remote_obvious_test_record_count"], 3)
        self.assertNotIn("权限测试", str(result))

    def test_dedupe_dry_run_prefers_local_binding(self):
        local = transaction_service.create_transaction(
            {
                "date": "2026-06-14",
                "type": "支出",
                "category": "餐饮",
                "amount": 25,
                "description": "private",
            },
            auto_sync=False,
        )
        with ledger.connect() as conn:
            conn.execute(
                """
                UPDATE transactions SET feishu_record_id = ?
                WHERE transaction_uid = ?
                """,
                ("rec-old", local["transaction_uid"]),
            )
        service = FakeRemoteService(
            [
                remote_record(
                    "rec-old", local["transaction_uid"], 1, created=1
                ),
                remote_record(
                    "rec-new", local["transaction_uid"], 1, created=2
                ),
            ]
        )
        result = bitable_sync.dedupe_remote(
            apply=False,
            service=service,
        )
        self.assertEqual(result["planned_delete_count"], 1)
        self.assertEqual(
            result["planned_deletions"][0]["record_id"],
            "rec-new",
        )
        self.assertEqual(service.deleted, [])

    def test_cleanup_test_records_dry_run_is_nondestructive(self):
        service = FakeRemoteService(
            [
                remote_record("rec-empty", "", 0),
                remote_record("rec-test", "test_probe", 9),
                remote_record(
                    "rec-permission",
                    "normal-uid",
                    10,
                    "权限测试",
                ),
                remote_record("rec-real", "real-uid", 11, "normal"),
            ]
        )
        result = bitable_sync.cleanup_test_records(
            apply=False,
            service=service,
        )
        self.assertEqual(result["planned_delete_count"], 3)
        self.assertEqual(service.deleted, [])

    def test_list_tables_returns_counts_and_marks_original(self):
        service = FakeTableService()
        result = bitable_sync.list_tables(service=service)
        self.assertTrue(result["success"])
        self.assertEqual(result["original_table_id"], "tbl-original")
        self.assertEqual(result["table_count"], 2)
        original = next(
            item for item in result["tables"] if item["is_original"]
        )
        self.assertEqual(original["table_id"], "tbl-original")
        self.assertEqual(original["field_count"], 2)
        self.assertEqual(original["record_count"], 1)

    def test_field_map_contains_derived_fields(self):
        for key, label in DERIVED_FIELD_LABELS.items():
            self.assertIn(key, bitable_sync.FIELD_MAP)
            self.assertEqual(bitable_sync.FIELD_MAP[key], label)
            self.assertIn(label, bitable_sync.REQUIRED_FIELDS)

    def test_ensure_main_table_fields_only_creates_missing_fields(self):
        service = FakeTableService()
        result = bitable_sync.ensure_main_table_fields(service=service)
        self.assertTrue(result["success"])
        self.assertEqual(result["table_id"], "tbl-original")
        self.assertEqual(result["created_count"], len(DERIVED_FIELD_LABELS))
        self.assertEqual(result["created_summary_tables"], 0)
        self.assertEqual(service.deleted, [])
        self.assertEqual(
            {item["field_name"] for item in service.created_fields},
            set(DERIVED_FIELD_LABELS.values()),
        )
        self.assertTrue(
            all(item["table_id"] == "tbl-original" for item in service.created_fields)
        )

    def test_cleanup_summary_tables_dry_run_is_nondestructive(self):
        service = FakeTableService()
        result = bitable_sync.cleanup_summary_tables(
            apply=False,
            service=service,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["mode"], "dry-run")
        self.assertEqual(result["planned_delete_count"], 1)
        self.assertEqual(
            result["planned_deletions"][0]["table_id"],
            "tbl-summary",
        )
        self.assertEqual(service.deleted, [])

    def test_cleanup_summary_tables_apply_requires_confirmation(self):
        service = FakeTableService()
        result = bitable_sync.cleanup_summary_tables(
            apply=True,
            service=service,
        )
        self.assertFalse(result["success"])
        self.assertIn(
            "--confirm-delete-summary-tables",
            result["message"],
        )
        self.assertEqual(service.deleted, [])

    def test_cleanup_summary_tables_never_deletes_original_table(self):
        service = FakeTableService(
            [
                {"name": "原始数据表", "table_id": "tbl-original"},
                {"name": "月度汇总表", "table_id": "tbl-summary"},
                {"name": "分类月度汇总表", "table_id": "tbl-category"},
            ]
        )
        result = bitable_sync.cleanup_summary_tables(
            apply=True,
            confirm_delete_summary_tables=True,
            service=service,
        )
        self.assertTrue(result["success"])
        self.assertEqual(
            service.deleted,
            ["tbl-summary", "tbl-category"],
        )
        self.assertNotIn("tbl-original", service.deleted)
        self.assertTrue(result["original_table_protected"])

    def test_backfill_derived_fields_dry_run_is_nondestructive(self):
        record = transaction_service.create_transaction(
            {
                "date": "2026-06-14",
                "type": "支出",
                "category": "餐饮",
                "amount": 10.4,
                "description": "午饭",
                "tags": "食堂",
            },
            auto_sync=False,
        )
        with ledger.connect() as conn:
            conn.execute(
                f"""
                UPDATE transactions
                SET {", ".join(f"{column} = NULL" for column in DERIVED_COLUMNS)},
                    sync_status = 'synced'
                WHERE transaction_uid = ?
                """,
                (record["transaction_uid"],),
            )
        result = bitable_sync.backfill_derived_fields(apply=False)
        self.assertEqual(result["planned_update_count"], 1)
        self.assertEqual(result["updated_count"], 0)
        with ledger.connect() as conn:
            net_amount, sync_status = conn.execute(
                """
                SELECT net_amount, sync_status FROM transactions
                WHERE transaction_uid = ?
                """,
                (record["transaction_uid"],),
            ).fetchone()
        self.assertIsNone(net_amount)
        self.assertEqual(sync_status, "synced")

    def test_backfill_derived_fields_apply_updates_sqlite_and_pending(self):
        record = transaction_service.create_transaction(
            {
                "date": "2026-06-14",
                "type": "收入",
                "category": "工资",
                "amount": 20000,
                "description": "工资",
                "tags": "工资",
            },
            auto_sync=False,
        )
        with ledger.connect() as conn:
            conn.execute(
                f"""
                UPDATE transactions
                SET {", ".join(f"{column} = NULL" for column in DERIVED_COLUMNS)},
                    sync_status = 'synced'
                WHERE transaction_uid = ?
                """,
                (record["transaction_uid"],),
            )
        result = bitable_sync.backfill_derived_fields(apply=True)
        self.assertEqual(result["planned_update_count"], 1)
        self.assertEqual(result["updated_count"], 1)
        with ledger.connect() as conn:
            net_amount, ledger_month, sync_status = conn.execute(
                """
                SELECT net_amount, ledger_month, sync_status FROM transactions
                WHERE transaction_uid = ?
                """,
                (record["transaction_uid"],),
            ).fetchone()
            outbox_count = conn.execute(
                """
                SELECT COUNT(*) FROM sync_outbox
                WHERE transaction_uid = ? AND operation = 'update'
                """,
                (record["transaction_uid"],),
            ).fetchone()[0]
        self.assertEqual(net_amount, 20000)
        self.assertEqual(ledger_month, "2026-06")
        self.assertEqual(sync_status, "pending")
        self.assertGreaterEqual(outbox_count, 1)

    def test_audit_derived_fields_reports_local_and_remote_field_status(self):
        record = transaction_service.create_transaction(
            {
                "date": "2026-06-14",
                "type": "支出",
                "category": "餐饮",
                "amount": 10.4,
                "description": "private description",
                "tags": "早餐",
            },
            auto_sync=False,
        )
        with ledger.connect() as conn:
            conn.execute(
                f"""
                UPDATE transactions
                SET {", ".join(f"{column} = NULL" for column in DERIVED_COLUMNS)}
                WHERE transaction_uid = ?
                """,
                (record["transaction_uid"],),
            )
        service = FakeFieldService(
            {
                "success": True,
                "code": 0,
                "message": "",
                "log_id": "log-fields",
                "fields": required_field_defs(),
            }
        )
        result = bitable_sync.audit_derived_fields(service=service)
        self.assertTrue(result["success"])
        self.assertEqual(result["local_transactions_total"], 1)
        self.assertEqual(result["active_missing_derived_count"], 1)
        self.assertFalse(result["recent_records"][0]["derived_complete"])
        self.assertIn(
            "date_year",
            result["recent_records"][0]["missing_fields"],
        )
        self.assertTrue(
            result["feishu_original_table_fields"][
                "all_derived_fields_present"
            ]
        )
        self.assertEqual(
            set(result["feishu_original_table_fields"]["existing_derived_fields"]),
            set(DERIVED_FIELD_LABELS.values()),
        )
        self.assertNotIn("private description", str(result))

    def test_sync_one_returns_only_safe_identity_fields(self):
        record = transaction_service.create_transaction(
            {
                "date": "2026-06-14",
                "type": "支出",
                "category": "餐饮",
                "amount": 25,
                "description": "private description",
            },
            auto_sync=False,
        )
        service = FakeFieldService(
            {
                "success": True,
                "code": 0,
                "message": "",
                "log_id": "log-fields",
                "fields": required_field_defs(),
            }
        )
        service.search_record = mock.Mock(
            return_value={
                "success": True,
                "code": 0,
                "message": "",
                "log_id": "log-search",
                "record_id": None,
            }
        )
        service.create_record = mock.Mock(
            return_value={
                "success": False,
                "code": 1255040,
                "message": "request timeout",
                "log_id": "log-one",
            }
        )
        result = bitable_sync.sync_one_pending(service=service)
        self.assertEqual(result["local_id"], record["id"])
        self.assertEqual(
            result["transaction_uid_prefix"],
            record["transaction_uid"][:8],
        )
        self.assertNotIn(record["transaction_uid"], result.values())
        self.assertNotIn("description", result)
        self.assertEqual(result["code"], 1255040)
        self.assertEqual(result["log_id"], "log-one")
        self.assertEqual(
            [step["step"] for step in result["steps"]],
            ["search", "create"],
        )
        self.assertFalse(result["steps"][0]["record_id_found"])
        self.assertNotIn("record_id", result["steps"][0])
        create_payload = service.create_record.call_args.args[0]
        for column in DERIVED_COLUMNS:
            self.assertIn(column, create_payload)
            self.assertIsNotNone(create_payload[column], column)
        self.assertEqual(create_payload["expense_amount"], 25.0)
        self.assertEqual(create_payload["net_amount"], -25.0)
        self.assertEqual(create_payload["is_expense"], 1)

    def test_sync_one_traces_update_when_record_exists(self):
        record = transaction_service.create_transaction(
            {
                "date": "2026-06-14",
                "type": "支出",
                "category": "交通",
                "amount": 4,
                "description": "private",
            },
            auto_sync=False,
        )
        service = FakeFieldService(
            {
                "success": True,
                "code": 0,
                "message": "",
                "log_id": "log-fields",
                "fields": required_field_defs(),
            }
        )
        service.search_record = mock.Mock(
            return_value={
                "success": True,
                "code": 0,
                "message": "",
                "log_id": "log-search",
                "record_id": "rec-private",
            }
        )
        service.update_record = mock.Mock(
            return_value={
                "success": True,
                "code": 0,
                "message": "ok",
                "log_id": "log-update",
                "record_id": "rec-private",
            }
        )
        result = bitable_sync.sync_one_pending(service=service)
        self.assertEqual(result["local_id"], record["id"])
        self.assertTrue(result["success"])
        self.assertEqual(
            [step["step"] for step in result["steps"]],
            ["search", "update"],
        )
        self.assertTrue(result["steps"][0]["record_id_found"])
        self.assertNotIn("rec-private", str(result))
        update_payload = service.update_record.call_args.args[1]
        for column in DERIVED_COLUMNS:
            self.assertIn(column, update_payload)
            self.assertIsNotNone(update_payload[column], column)
        self.assertEqual(update_payload["expense_amount"], 4.0)
        self.assertEqual(update_payload["net_amount"], -4.0)
        self.assertEqual(update_payload["is_expense"], 1)

    def test_timeout_is_bounded(self):
        with mock.patch.dict(
            "os.environ",
            {"FEISHU_BITABLE_TIMEOUT_SECONDS": "1"},
        ):
            self.assertEqual(bitable_sync._api_timeout_seconds(), 3.0)
        with mock.patch.dict(
            "os.environ",
            {"FEISHU_BITABLE_TIMEOUT_SECONDS": "120"},
        ):
            self.assertEqual(bitable_sync._api_timeout_seconds(), 60.0)


if __name__ == "__main__":
    unittest.main()

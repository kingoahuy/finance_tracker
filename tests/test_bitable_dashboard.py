import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from finance_tracker import bitable_dashboard


class FakeDashboardService:
    def __init__(self):
        self.config = SimpleNamespace(bitable_app_token="app-token")
        self.tables = {}
        self.fields = {}
        self.records = defaultdict(list)
        self.next_table = 1
        self.next_record = 1

    def list_tables(self):
        return {
            "success": True,
            "code": 0,
            "message": "",
            "log_id": "",
            "tables": [
                {"name": name, "table_id": table_id}
                for name, table_id in self.tables.items()
            ],
        }

    def create_table(self, name):
        table_id = f"tbl-{self.next_table}"
        self.next_table += 1
        self.tables[name] = table_id
        self.fields[table_id] = []
        return {
            "success": True, "code": 0, "message": "",
            "log_id": "", "table_id": table_id,
        }

    def list_fields(self, table_id):
        return {
            "success": True, "code": 0, "message": "", "log_id": "",
            "fields": list(self.fields[table_id]),
        }

    def create_field(self, table_id, field_name, field_type):
        self.fields[table_id].append(
            {"field_name": field_name, "type": field_type}
        )
        return {"success": True, "code": 0, "message": "", "log_id": ""}

    def list_records(self, table_id):
        return {
            "success": True, "code": 0, "message": "", "log_id": "",
            "records": list(self.records[table_id]),
        }

    def batch_create(self, table_id, rows):
        for item in rows:
            self.records[table_id].append(
                {
                    "record_id": f"rec-{self.next_record}",
                    "fields": dict(item["fields"]),
                }
            )
            self.next_record += 1
        return {"success": True, "code": 0, "message": "", "log_id": ""}

    def batch_update(self, table_id, rows):
        by_id = {item["record_id"]: item for item in self.records[table_id]}
        for item in rows:
            by_id[item["record_id"]]["fields"] = dict(item["fields"])
        return {"success": True, "code": 0, "message": "", "log_id": ""}

    def batch_delete(self, table_id, record_ids):
        record_ids = set(record_ids)
        self.records[table_id] = [
            item for item in self.records[table_id]
            if item["record_id"] not in record_ids
        ]
        return {"success": True, "code": 0, "message": "", "log_id": ""}


from collections import defaultdict


class BitableDashboardTest(unittest.TestCase):
    def setUp(self):
        self.service = FakeDashboardService()

    def test_check_creates_all_tables_and_fields(self):
        result = bitable_dashboard.check_dashboard(self.service)
        self.assertTrue(result["success"])
        self.assertEqual(len(result["tables"]), 11)
        for name, definition in bitable_dashboard.TABLE_DEFINITIONS.items():
            table_id = self.service.tables[name]
            field_names = {
                item["field_name"] for item in self.service.fields[table_id]
            }
            self.assertEqual(field_names, set(definition["fields"]))

    @mock.patch.object(bitable_dashboard, "build_dashboard_rows")
    def test_sync_is_idempotent(self, build_rows):
        build_rows.return_value = {
            name: (
                [{"年月": "2026-06", "收入": 100, "支出": 20,
                  "净收入": 80, "结余率": 80, "支出笔数": 1,
                  "收入笔数": 1}]
                if name == "月度汇总表"
                else []
            )
            for name in bitable_dashboard.TABLE_DEFINITIONS
        }
        first = bitable_dashboard._sync(
            ["2026-06"], include_overview=True, service=self.service
        )
        second = bitable_dashboard._sync(
            ["2026-06"], include_overview=True, service=self.service
        )
        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        table_id = self.service.tables["月度汇总表"]
        self.assertEqual(len(self.service.records[table_id]), 1)

    def test_build_rows_contains_all_summary_tables(self):
        with mock.patch.object(
            bitable_dashboard.analytics,
            "get_monthly_trend",
            return_value=[],
        ):
            rows = bitable_dashboard.build_dashboard_rows(
                [], include_overview=False
            )
        self.assertEqual(set(rows), set(bitable_dashboard.TABLE_DEFINITIONS))

    @mock.patch.object(bitable_dashboard.analytics, "get_monthly_trend")
    def test_sync_month_builds_monthly_summary(self, monthly_trend):
        monthly_trend.return_value = [
            {
                "month": "2026-06",
                "income": 100,
                "expense": 20,
                "net_income": 80,
                "savings_rate": 80,
                "expense_count": 1,
                "income_count": 1,
            }
        ]
        rows = bitable_dashboard.build_dashboard_rows(
            ["2026-06"], include_overview=False
        )
        self.assertEqual(len(rows["月度汇总表"]), 1)

    def test_audit_reports_duplicate_keys(self):
        bitable_dashboard.check_dashboard(self.service)
        table_id = self.service.tables["月度汇总表"]
        fields = {
            "年月": "2026-06", "收入": 1, "支出": 1, "净收入": 0,
            "结余率": 0, "支出笔数": 1, "收入笔数": 1,
        }
        self.service.records[table_id] = [
            {"record_id": "rec-1", "fields": fields},
            {"record_id": "rec-2", "fields": fields},
        ]
        with mock.patch.object(
            bitable_dashboard.analytics,
            "get_monthly_trend",
            return_value=[{"month": "2026-06"}],
        ):
            result = bitable_dashboard.audit_dashboard(self.service)
        monthly = next(
            item for item in result["tables"]
            if item["table"] == "月度汇总表"
        )
        self.assertEqual(monthly["duplicate_key_count"], 1)

    def test_numeric_key_matches_remote_string_number(self):
        definition = bitable_dashboard.TABLE_DEFINITIONS["大额支出表"]
        local = {
            "年月": "2026-06",
            "日期": 1780588800000,
            "分类": "交通",
            "金额": 549.0,
            "描述": "机票",
            "标签": ["旅游"],
        }
        remote = {**local, "金额": "549"}
        self.assertEqual(
            bitable_dashboard._record_key(local, definition),
            bitable_dashboard._record_key(remote, definition),
        )

    def test_generate_guide_writes_complete_markdown_without_api(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "guide.md"
            result = bitable_dashboard.generate_dashboard_guide(path)
            content = path.read_text(encoding="utf-8")
        self.assertTrue(result["success"])
        self.assertEqual(result["section_count"], 7)
        for heading in (
            "总览区", "趋势区", "分类区", "预算区",
            "结构区", "明细区", "洞察区",
        ):
            self.assertIn(heading, content)
        self.assertIn("手机端布局", content)
        self.assertIn("每月更新步骤", content)
        self.assertIn("常见问题排查", content)
        self.assertIn("必须在飞书端手动完成", content)


if __name__ == "__main__":
    unittest.main()

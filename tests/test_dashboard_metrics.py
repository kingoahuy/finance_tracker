import tempfile
import unittest
from pathlib import Path

from finance_tracker import dashboard_metrics, ledger
from finance_tracker.bitable_sync import dashboard_row_to_bitable_fields


class DashboardMetricsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = ledger.DB_FILE
        ledger.DB_FILE = Path(self.temp_dir.name) / "dashboard.db"
        ledger.init_db()

    def tearDown(self):
        ledger.DB_FILE = self.original_db
        self.temp_dir.cleanup()

    def test_calendar_day_averages_include_zero_transaction_days(self):
        ledger.add_transaction(
            {
                "date": "2026-01-01",
                "type": "收入",
                "category": "工资",
                "amount": 100,
                "description": "收入",
            }
        )
        ledger.add_transaction(
            {
                "date": "2026-01-01",
                "type": "支出",
                "category": "餐饮",
                "amount": 30,
                "description": "用餐补买早餐",
            }
        )
        ledger.add_transaction(
            {
                "date": "2026-01-03",
                "type": "支出",
                "category": "餐饮",
                "amount": 30,
                "description": "晚餐",
            }
        )
        ledger.add_transaction(
            {
                "date": "2026-01-03",
                "type": "支出",
                "category": "其他",
                "amount": 20,
                "description": "代付",
                "tags": "个人垫付",
            }
        )

        rows = dashboard_metrics.build_daily_dashboard_rows("2026-01-03")

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["daily_expense"], 0.0)
        self.assertEqual(rows[-1]["mtd_income"], 100.0)
        self.assertEqual(rows[-1]["mtd_expense"], 60.0)
        self.assertEqual(rows[-1]["mtd_daily_avg_income"], 33.33)
        self.assertEqual(rows[-1]["mtd_daily_avg_expense"], 20.0)
        self.assertEqual(rows[-1]["mtd_meal_subsidy_expense"], 30.0)
        self.assertEqual(rows[-1]["mtd_meal_subsidy_expense_share"], 50.0)
        self.assertEqual(rows[-1]["ytd_meal_subsidy_expense"], 30.0)
        self.assertEqual(rows[-1]["ytd_meal_subsidy_expense_share"], 50.0)
        self.assertEqual(rows[-1]["advance_balance"], 20.0)
        self.assertTrue(rows[-1]["is_latest"])

    def test_bitable_mapping_keeps_numbers_dates_and_flags_simple(self):
        row = dashboard_metrics.build_daily_dashboard_rows("2026-01-01")[0]
        fields = dashboard_row_to_bitable_fields(row)

        self.assertEqual(fields["指标日期"], "2026-01-01")
        self.assertIsInstance(fields["日期"], int)
        self.assertIsInstance(fields["MTD日均支出"], float)
        self.assertTrue(fields["是否最新"])


if __name__ == "__main__":
    unittest.main()

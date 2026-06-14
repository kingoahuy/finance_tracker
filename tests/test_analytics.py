import tempfile
import unittest
from pathlib import Path

from finance_tracker import analytics, ledger, transaction_service


class AnalyticsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = ledger.DB_FILE
        ledger.DB_FILE = Path(self.temp_dir.name) / "analytics.db"
        ledger.init_db()
        self._add("2026-06-01", "收入", "工资", 5000, "工资", "工资", 0, 0)
        self._add("2026-06-02", "支出", "餐饮", 100, "聚餐", "聚餐,餐饮", 0, 0)
        self._add("2026-06-03", "支出", "交通", 20, "地铁", "通勤,交通", 1, 0)
        self._add("2026-06-04", "支出", "居住", 1000, "房租", "居住,固定支出", 1, 1)
        self._add("2026-05-03", "支出", "购物", 200, "日用品", "购物", 1, 0)
        deleted = self._add(
            "2026-06-05", "支出", "娱乐", 9999, "已删除测试", "娱乐", 0, 0
        )
        with ledger.connect() as conn:
            conn.execute(
                "UPDATE transactions SET status = 'deleted' WHERE transaction_uid = ?",
                (deleted["transaction_uid"],),
            )

    def tearDown(self):
        ledger.DB_FILE = self.original_db
        self.temp_dir.cleanup()

    def _add(self, date, txn_type, category, amount, description, tags, need, fixed):
        return transaction_service.create_transaction(
            {
                "date": date,
                "type": txn_type,
                "category": category,
                "amount": amount,
                "description": description,
                "tags": tags,
                "is_need": need,
                "is_fixed": fixed,
            },
            auto_sync=False,
        )

    def test_finance_overview_structure_and_active_filter(self):
        result = analytics.get_finance_overview("2026-06-01", "2026-06-30")
        self.assertEqual(
            set(result),
            {
                "total_income", "total_expense", "net_income", "savings_rate",
                "transaction_count", "average_daily_expense", "largest_expense",
                "largest_expense_category", "current_month_income",
                "current_month_expense", "current_month_balance", "today_expense",
            },
        )
        self.assertEqual(result["total_expense"], 1120.0)
        self.assertEqual(result["transaction_count"], 4)

    def test_monthly_trend_structure(self):
        result = analytics.get_monthly_trend()
        self.assertEqual(
            set(result[0]),
            {
                "month", "income", "expense", "net_income", "savings_rate",
                "expense_count", "income_count",
            },
        )

    def test_category_expense_summary_structure(self):
        result = analytics.get_category_expense_summary("2026-06")
        self.assertEqual(
            set(result[0]),
            {"month", "category", "amount", "share", "count", "average_daily_amount"},
        )

    def test_daily_expense_trend_structure(self):
        result = analytics.get_daily_expense_trend("2026-06")
        self.assertEqual(len(result), 30)
        self.assertEqual(
            set(result[0]),
            {"date", "month", "expense", "expense_count", "moving_average_7d"},
        )

    def test_income_source_summary_structure(self):
        result = analytics.get_income_source_summary("2026-06")
        self.assertEqual(
            set(result[0]),
            {"month", "income_category", "amount", "share", "count"},
        )

    def test_tag_summary_splits_comma_separated_tags(self):
        result = analytics.get_tag_summary("2026-06")
        by_tag = {item["tag"]: item for item in result}
        self.assertIn("通勤", by_tag)
        self.assertEqual(
            set(by_tag["通勤"]),
            {"month", "tag", "amount", "count", "related_categories"},
        )
        self.assertEqual(by_tag["通勤"]["related_categories"], ["交通"])

    def test_need_vs_want_summary_structure(self):
        result = analytics.get_need_vs_want_summary("2026-06")
        self.assertEqual({"刚需", "非刚需"}, {item["type"] for item in result})
        self.assertEqual(set(result[0]), {"month", "type", "amount", "share"})

    def test_fixed_vs_variable_summary_structure(self):
        result = analytics.get_fixed_vs_variable_summary("2026-06")
        self.assertEqual(
            {"固定支出", "变动支出"},
            {item["type"] for item in result},
        )
        self.assertEqual(set(result[0]), {"month", "type", "amount", "share"})

    def test_top_expenses_structure_and_limit(self):
        result = analytics.get_top_expenses("2026-06", limit=2)
        self.assertEqual(len(result), 2)
        self.assertEqual(
            set(result[0]),
            {"month", "date", "category", "amount", "description", "tags"},
        )
        self.assertNotEqual(result[0]["amount"], 9999)

    def test_budget_warning_structure(self):
        result = analytics.get_budget_warning("2026-06")
        self.assertEqual(
            set(result[0]),
            {
                "month", "category", "budget", "used", "remaining",
                "usage_rate", "status",
            },
        )
        self.assertTrue(
            {item["status"] for item in result}.issubset(
                {"正常", "接近超支", "已超支"}
            )
        )

    def test_generate_finance_insights_structure(self):
        result = analytics.generate_finance_insights("2026-06")
        self.assertEqual(
            set(result),
            {
                "month", "summary", "primary_expense_category",
                "abnormal_expenses", "saving_advice", "next_month_reminder",
            },
        )
        self.assertNotIn("已删除测试", str(result))


if __name__ == "__main__":
    unittest.main()

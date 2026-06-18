import datetime
import unittest

from finance_tracker.derived_fields import (
    DATA_VERSION,
    clean_tags,
    enrich_transaction_fields,
)


class DerivedFieldsTest(unittest.TestCase):
    def test_expense_fields_are_generated(self):
        result = enrich_transaction_fields(
            {
                "date": "2026-06-14",
                "type": "支出",
                "amount": 10.4,
                "tags": " 食堂, 刚需，，通勤 ",
                "status": "active",
            }
        )
        self.assertEqual(result["date_year"], 2026)
        self.assertEqual(result["date_year_month"], "2026-06")
        self.assertEqual(result["date_month"], 6)
        self.assertEqual(result["date_day"], 14)
        self.assertEqual(result["date_weekday"], "周日")
        self.assertEqual(
            result["date_week_number"],
            datetime.date(2026, 6, 14).isocalendar().week,
        )
        self.assertEqual(result["date_quarter"], "2026Q2")
        self.assertEqual(result["income_amount"], 0.0)
        self.assertEqual(result["expense_amount"], 10.4)
        self.assertEqual(result["net_amount"], -10.4)
        self.assertEqual(result["amount_bucket"], "0-20")
        self.assertEqual(result["tags_text"], "食堂, 刚需, 通勤")
        self.assertEqual(result["is_income"], 0)
        self.assertEqual(result["is_expense"], 1)
        self.assertEqual(result["is_active"], 1)
        self.assertEqual(result["ledger_month"], "2026-06")
        self.assertEqual(result["data_version"], DATA_VERSION)
        self.assertEqual(result["_derived_error"], "")

    def test_large_income_net_amount_is_positive(self):
        result = enrich_transaction_fields(
            {
                "date": "2026-01-01",
                "type": "收入",
                "amount": 20000,
                "tags": ["工资", "工资", "奖金"],
                "status": "active",
            }
        )
        self.assertEqual(result["income_amount"], 20000.0)
        self.assertEqual(result["expense_amount"], 0.0)
        self.assertEqual(result["net_amount"], 20000.0)
        self.assertEqual(result["amount_bucket"], "500+")
        self.assertEqual(result["tags_text"], "工资, 奖金")
        self.assertEqual(result["is_income"], 1)
        self.assertEqual(result["is_expense"], 0)

    def test_deleted_record_is_not_active(self):
        result = enrich_transaction_fields(
            {
                "date": "2026-06-14",
                "type": "支出",
                "amount": 25,
                "status": "deleted",
            }
        )
        self.assertEqual(result["is_active"], 0)
        self.assertEqual(result["net_amount"], -25.0)

    def test_invalid_date_returns_diagnostic_error(self):
        result = enrich_transaction_fields(
            {"date": "not-a-date", "type": "支出", "amount": 25}
        )
        self.assertIsNone(result["date_year"])
        self.assertEqual(result["date_year_month"], "")
        self.assertIn("日期格式无效", result["_derived_error"])

    def test_clean_tags_removes_empty_items_and_duplicates(self):
        self.assertEqual(
            clean_tags("餐饮,, 餐饮，刚需、通勤; "),
            ["餐饮", "刚需", "通勤"],
        )


if __name__ == "__main__":
    unittest.main()

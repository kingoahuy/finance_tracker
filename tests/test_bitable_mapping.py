import datetime
import unittest

from finance_tracker.derived_fields import DERIVED_FIELD_LABELS
from finance_tracker.bitable_sync import FIELD_MAP, transaction_to_bitable_fields


class BitableMappingTest(unittest.TestCase):
    def test_mapping_types(self):
        fields = transaction_to_bitable_fields(
            {
                "transaction_uid": "uid-1",
                "id": 12,
                "date": "2026-06-13",
                "type": "支出",
                "category": "餐饮",
                "amount": 25.5,
                "description": "午饭",
                "tags": "旅行,刚需",
                "is_need": 1,
                "is_fixed": 0,
                "source": "feishu",
                "source_message_id": "msg-1",
                "created_at": "2026-06-13 12:00:00",
                "updated_at": "2026-06-13 12:01:00",
            }
        )
        self.assertEqual(fields[FIELD_MAP["amount"]], 25.5)
        self.assertEqual(
            fields[FIELD_MAP["date"]],
            int(datetime.datetime(2026, 6, 13).timestamp() * 1000),
        )
        self.assertTrue(fields[FIELD_MAP["is_need"]])
        self.assertFalse(fields[FIELD_MAP["is_fixed"]])
        self.assertEqual(fields[FIELD_MAP["tags"]], ["旅行", "刚需"])
        self.assertEqual(fields[FIELD_MAP["type"]], "支出")
        self.assertEqual(fields[FIELD_MAP["date_year"]], 2026)
        self.assertEqual(fields[FIELD_MAP["date_year_month"]], "2026-06")
        self.assertEqual(fields[FIELD_MAP["date_month"]], 6)
        self.assertEqual(fields[FIELD_MAP["date_day"]], 13)
        self.assertEqual(fields[FIELD_MAP["date_weekday"]], "周六")
        self.assertEqual(fields[FIELD_MAP["date_quarter"]], "2026Q2")
        self.assertEqual(fields[FIELD_MAP["income_amount"]], 0.0)
        self.assertEqual(fields[FIELD_MAP["expense_amount"]], 25.5)
        self.assertEqual(fields[FIELD_MAP["net_amount"]], -25.5)
        self.assertEqual(fields[FIELD_MAP["amount_bucket"]], "20-50")
        self.assertEqual(fields[FIELD_MAP["tags_text"]], "旅行, 刚需")
        self.assertFalse(fields[FIELD_MAP["is_income"]])
        self.assertTrue(fields[FIELD_MAP["is_expense"]])
        self.assertTrue(fields[FIELD_MAP["is_active"]])
        self.assertEqual(fields[FIELD_MAP["ledger_month"]], "2026-06")
        self.assertEqual(fields[FIELD_MAP["data_version"]], "derived-v1")
        for label in DERIVED_FIELD_LABELS.values():
            self.assertIn(label, fields)


if __name__ == "__main__":
    unittest.main()

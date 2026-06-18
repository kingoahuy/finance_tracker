import tempfile
import unittest
from pathlib import Path

from finance_tracker import ledger, tagging, transaction_service


class TagGenerationTest(unittest.TestCase):
    def assertTagsContain(self, transaction, *expected):
        tags = set(tagging.generate_tags(transaction).split(","))
        for tag in expected:
            self.assertIn(tag, tags)
        self.assertLessEqual(len(tags), 5)

    def test_canteen_meal_has_meaningful_tags(self):
        tags = set(
            tagging.generate_tags(
                {
                    "date": "2026-06-14",
                    "type": "支出",
                    "category": "餐饮",
                    "amount": 10.4,
                    "description": "食堂吃饭",
                    "is_need": True,
                }
            ).split(",")
        )
        self.assertTrue(tags.intersection({"食堂", "餐饮", "刚需"}))

    def test_subway_has_commute_tag(self):
        tags = set(
            tagging.generate_tags(
                {
                    "date": "2026-06-15",
                    "type": "支出",
                    "category": "交通",
                    "amount": 4,
                    "description": "坐地铁",
                    "is_need": True,
                }
            ).split(",")
        )
        self.assertTrue(tags.intersection({"通勤", "交通"}))

    def test_coffee_has_coffee_tag(self):
        self.assertTagsContain(
            {
                "date": "2026-06-15",
                "type": "支出",
                "category": "餐饮",
                "amount": 16,
                "description": "咖啡",
            },
            "咖啡",
        )

    def test_rent_has_housing_and_fixed_tags(self):
        self.assertTagsContain(
            {
                "date": "2026-06-01",
                "type": "支出",
                "category": "居住",
                "amount": 3000,
                "description": "房租",
                "is_need": True,
                "is_fixed": True,
            },
            "居住",
            "固定支出",
        )

    def test_hainan_ticket_has_trip_tags(self):
        self.assertTagsContain(
            {
                "date": "2026-06-02",
                "type": "支出",
                "category": "娱乐",
                "amount": 127,
                "description": "海南旅游门票",
            },
            "旅游",
            "门票",
            "2026海南旅游",
        )

    def test_ai_tags_are_kept_and_local_tags_are_merged(self):
        tags = tagging.generate_tags(
            {
                "date": "2026-06-15",
                "type": "支出",
                "category": "餐饮",
                "amount": 16,
                "description": "咖啡",
                "tags": ["AI自定义", "咖啡"],
            }
        ).split(",")
        self.assertEqual(tags[0], "AI自定义")
        self.assertIn("咖啡", tags)
        self.assertGreater(len(tags), 2)

    def test_empty_tags_get_category_fallback(self):
        tags = tagging.generate_tags(
            {
                "date": "invalid",
                "type": "收入",
                "category": "兼职",
                "amount": 100,
                "description": "",
            }
        )
        self.assertTrue(tags)
        self.assertIn("兼职", tags.split(","))


class TagBackfillTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = ledger.DB_FILE
        ledger.DB_FILE = Path(self.temp_dir.name) / "test.db"
        ledger.init_db()
        self.active = transaction_service.create_transaction(
            {
                "date": "2026-06-15",
                "type": "支出",
                "category": "交通",
                "amount": 4,
                "description": "地铁",
            },
            auto_sync=False,
        )
        with ledger.connect() as conn:
            conn.execute(
                """
                UPDATE transactions
                SET tags = '', tags_text = '', sync_status = 'synced'
                WHERE transaction_uid = ?
                """,
                (self.active["transaction_uid"],),
            )

    def tearDown(self):
        ledger.DB_FILE = self.original_db
        self.temp_dir.cleanup()

    def _row(self):
        with ledger.connect() as conn:
            return conn.execute(
                """
                SELECT tags, sync_status, tags_text
                FROM transactions
                WHERE transaction_uid = ?
                """,
                (self.active["transaction_uid"],),
            ).fetchone()

    def test_dry_run_does_not_write_database(self):
        result = tagging.backfill_tags(apply=False)
        self.assertEqual(result["planned_count"], 1)
        self.assertEqual(result["updated_count"], 0)
        self.assertEqual(self._row(), ("", "synced", ""))

    def test_apply_updates_tags_and_sync_status(self):
        result = tagging.backfill_tags(apply=True)
        tags, sync_status, tags_text = self._row()
        self.assertEqual(result["updated_count"], 1)
        self.assertIn("通勤", tags.split(","))
        self.assertIn("通勤", tags_text.split(", "))
        self.assertEqual(sync_status, "pending")


if __name__ == "__main__":
    unittest.main()

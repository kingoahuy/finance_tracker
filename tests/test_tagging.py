import tempfile
import unittest
from pathlib import Path

from finance_tracker import ledger, tagging, transaction_service


class TagGenerationTest(unittest.TestCase):
    def assertTagsContain(self, transaction, *expected):
        tags = set(tagging.generate_tags(transaction).split(","))
        for tag in expected:
            self.assertIn(tag, tags)
        self.assertEqual(len(tags), 3)

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

    def test_subway_has_specific_transport_scene(self):
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
        self.assertEqual(tags, {"地铁", "日常出行", "刚需"})

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

    def test_rent_uses_scene_tag_without_duplicate_fixed_dimension(self):
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
            "房租",
        )
        tags = tagging.generate_tags(
            {
                "date": "2026-06-01",
                "type": "支出",
                "category": "居住",
                "amount": 3000,
                "description": "房租",
                "is_need": True,
                "is_fixed": True,
            }
        ).split(",")
        self.assertNotIn("固定支出", tags)
        self.assertNotIn("居住", tags)

    def test_hainan_ticket_has_trip_tags(self):
        self.assertTagsContain(
            {
                "date": "2026-06-02",
                "type": "支出",
                "category": "娱乐",
                "amount": 127,
                "description": "海南旅游门票",
            },
            "门票",
            "2026海南旅游",
        )

    def test_advance_payment_keyword_adds_personal_advance_tag(self):
        self.assertTagsContain(
            {
                "date": "2026-07-09",
                "type": "支出",
                "category": "其他",
                "amount": 45,
                "description": "垫付标书费用",
            },
            "个人垫付",
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
        self.assertEqual(len(tags), 3)

    def test_membership_name_is_not_misread_as_fruit(self):
        tags = tagging.generate_tags(
            {
                "date": "2026-05-10",
                "type": "支出",
                "category": "娱乐",
                "amount": 11,
                "description": "苹果音乐会员",
            }
        ).split(",")
        self.assertEqual(tags, ["订阅", "休闲娱乐", "非刚需"])

    def test_no_specific_evidence_uses_clear_category_scene_fallback(self):
        tags = tagging.generate_tags(
            {
                "date": "invalid",
                "type": "收入",
                "category": "兼职",
                "amount": 100,
                "description": "",
            }
        )
        self.assertEqual(tags, "兼职,兼职收入,收入记录")

    def test_meal_subsidy_expense_gets_explicit_usage_tag(self):
        tags = tagging.generate_tags(
            {
                "date": "2026-07-18",
                "type": "支出",
                "category": "餐饮",
                "amount": 20,
                "description": "用餐补买午饭",
                "is_need": True,
            },
            preserve_existing=False,
        ).split(",")
        self.assertEqual(len(tags), 3)
        self.assertIn("餐补消费", tags)

    def test_common_historical_scenes_are_specific_and_not_missing(self):
        cases = {
            "洗澡": ("居住", "洗浴"),
            "打麻将": ("娱乐", "棋牌"),
            "下午去游泳": ("娱乐", "运动健身"),
            "爸爸转账": ("其他", "家庭支持"),
            "充值deepseek api": ("其他", "AI工具"),
        }
        for description, (category, expected) in cases.items():
            txn_type = "收入" if description == "爸爸转账" else "支出"
            tags = tagging.generate_tags(
                {
                    "date": "2026-07-18",
                    "type": txn_type,
                    "category": category,
                    "amount": 10,
                    "description": description,
                },
                preserve_existing=False,
            ).split(",")
            self.assertIn(expected, tags, description)

    def test_substring_collisions_do_not_create_wrong_scene(self):
        fruit = tagging.generate_tags(
            {"type": "支出", "category": "餐饮", "description": "晚上买水果"},
            preserve_existing=False,
        ).split(",")
        electric_bike = tagging.generate_tags(
            {"type": "支出", "category": "交通", "description": "电动车充电桩"},
            preserve_existing=False,
        ).split(",")
        self.assertIn("水果", fruit)
        self.assertNotIn("饮品", fruit)
        self.assertNotIn("铁路出行", electric_bike)


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
        self.assertEqual(len(tags.split(",")), 3)
        self.assertIn("地铁", tags.split(","))
        self.assertIn("地铁", tags_text.split(", "))
        self.assertEqual(sync_status, "pending")


if __name__ == "__main__":
    unittest.main()

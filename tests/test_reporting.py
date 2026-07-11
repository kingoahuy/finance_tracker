import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from finance_tracker import email_service, ledger, reporting


class ReportingPayloadTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = ledger.DB_FILE
        ledger.DB_FILE = Path(self.temp_dir.name) / "test.db"
        ledger.init_db()

    def tearDown(self):
        ledger.DB_FILE = self.original_db
        self.temp_dir.cleanup()

    def _insert(
        self,
        date,
        txn_type,
        category,
        amount,
        description,
        tags="",
        status="active",
        is_need=0,
        is_fixed=0,
    ):
        with ledger.connect() as conn:
            conn.execute(
                """
                INSERT INTO transactions
                    (date, type, category, amount, description, tags, status, is_need, is_fixed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    date,
                    txn_type,
                    category,
                    amount,
                    description,
                    tags,
                    status,
                    is_need,
                    is_fixed,
                ),
            )

    def _seed(self):
        self._insert(
            "2026-06-14",
            "支出",
            "餐饮",
            28,
            "午饭超过二十个字会被截断的描述",
            "午餐,食堂",
            is_need=1,
        )
        self._insert(
            "2026-06-14",
            "支出",
            "交通",
            36.5,
            "打车",
            "打车,通勤",
            is_need=1,
        )
        self._insert(
            "2026-06-15",
            "收入",
            "工资",
            20000,
            "工资",
            "工资",
        )
        self._insert(
            "2026-06-16",
            "支出",
            "娱乐",
            99,
            "已删除流水不应统计",
            "游戏",
            status="deleted",
        )
        self._insert(
            "2026-05-14",
            "支出",
            "餐饮",
            10,
            "上月午餐",
            "午餐",
        )

    def test_four_payloads_return_complete_structure(self):
        self._seed()

        monthly_bill = reporting.build_monthly_bill_payload("2026-06")
        daily = reporting.build_daily_report_payload("2026-06-14")
        tag_analysis = reporting.build_monthly_tag_analysis_payload("2026-06")
        consumption = reporting.build_monthly_consumption_report_payload("2026-06")

        self.assertEqual(monthly_bill["report_type"], "monthly_bill")
        self.assertIn("overview", monthly_bill)
        self.assertIn("budget", monthly_bill)
        self.assertIn("top_categories", monthly_bill)
        self.assertIn("top_tags", monthly_bill)
        self.assertIn("top_expenses", monthly_bill)
        self.assertIn("compare_previous_month", monthly_bill)

        self.assertEqual(daily["report_type"], "daily_report")
        self.assertIn("email_daily_report_markdown", daily)
        self.assertIn("category_summary", daily)
        self.assertIn("tag_summary", daily)
        self.assertIn("transactions", daily)
        self.assertIn("budget", daily)

        self.assertEqual(tag_analysis["report_type"], "monthly_tag_analysis")
        self.assertIn("tag_summary", tag_analysis)
        self.assertIn("tag_groups", tag_analysis)
        self.assertIn("刚需", tag_analysis["tag_groups"])
        self.assertIn("非刚需", tag_analysis["tag_groups"])
        self.assertIn("固定支出", tag_analysis["tag_groups"])
        self.assertIn("小额高频", tag_analysis["tag_groups"])

        self.assertEqual(consumption["report_type"], "monthly_consumption_report")
        self.assertIn("need_vs_want", consumption)
        self.assertIn("fixed_vs_variable", consumption)
        self.assertIn("daily_trend", consumption)

    def test_payloads_only_count_active_transactions(self):
        self._seed()
        payload = reporting.build_monthly_bill_payload("2026-06")

        self.assertEqual(payload["overview"]["expense"], 64.5)
        self.assertEqual(payload["overview"]["expense_count"], 2)
        self.assertNotIn(
            "已删除流水不应统计",
            [row["description"] for row in payload["top_expenses"]],
        )

    def test_payloads_exclude_personal_advance_and_report_it_separately(self):
        self._seed()
        self._insert(
            "2026-06-14",
            "支出",
            "其他",
            45,
            "垫付标书费用",
            "个人垫付,标书",
        )
        self._insert(
            "2026-06-15",
            "收入",
            "报销",
            10,
            "收到垫付回款",
            "个人垫付",
        )

        monthly = reporting.build_monthly_bill_payload("2026-06")
        daily = reporting.build_daily_report_payload("2026-06-14")

        self.assertEqual(monthly["overview"]["expense"], 64.5)
        self.assertEqual(monthly["overview"]["income"], 20000.0)
        self.assertEqual(monthly["personal_advance"]["advance_expense"], 45.0)
        self.assertEqual(monthly["personal_advance"]["advance_reimbursement"], 10.0)
        self.assertEqual(monthly["personal_advance"]["current_balance"], 35.0)
        self.assertNotIn(
            "个人垫付",
            [row["tag"] for row in monthly["top_tags"]],
        )

        self.assertEqual(daily["overview"]["expense"], 64.5)
        self.assertEqual(daily["personal_advance"]["advance_expense"], 45.0)
        self.assertEqual(daily["personal_advance"]["current_balance"], 45.0)

    def test_web_daily_report_excludes_personal_advance_from_key_metrics(self):
        df = pd.DataFrame(
            [
                {
                    "date": "2026-06-14",
                    "type": "支出",
                    "category": "餐饮",
                    "amount": 10.0,
                    "description": "早餐",
                    "tags": "早餐",
                },
                {
                    "date": "2026-06-14",
                    "type": "支出",
                    "category": "其他",
                    "amount": 45.0,
                    "description": "垫付标书费用",
                    "tags": "个人垫付,标书",
                },
            ]
        )

        markdown = email_service.generate_report_content(
            df,
            pd.Timestamp("2026-06-14").date(),
        )

        self.assertIn("| 今日支出 | ¥10.00 |", markdown)
        self.assertIn("| 本月累计支出 | ¥10.00 |", markdown)
        self.assertIn("| 今日垫付支出 | ¥45.00 |", markdown)
        self.assertIn("垫付标书费用", markdown)

    def test_tags_are_split_and_summarized(self):
        self._seed()
        payload = reporting.build_monthly_tag_analysis_payload("2026-06")
        tags = {row["tag"]: row for row in payload["tag_summary"]}

        self.assertIn("午餐", tags)
        self.assertIn("食堂", tags)
        self.assertIn("打车", tags)
        self.assertEqual(tags["午餐"]["amount"], 28.0)

    def test_daily_report_uses_email_report_content(self):
        self._seed()
        with mock.patch.object(reporting, "generate_report_content") as report_content:
            report_content.return_value = "# 邮件日报\n\n同源内容"
            payload = reporting.build_daily_report_payload("2026-06-14")

        self.assertEqual(payload["email_daily_report_markdown"], "# 邮件日报\n\n同源内容")
        report_content.assert_called_once()
        self.assertEqual(report_content.call_args.args[1].isoformat(), "2026-06-14")

    def test_empty_data_returns_complete_structure(self):
        payload = reporting.build_monthly_consumption_report_payload("2026-06")

        self.assertEqual(payload["overview"]["expense"], 0.0)
        self.assertEqual(payload["category_summary"], [])
        self.assertEqual(payload["tag_summary"], [])
        self.assertEqual(payload["top_expenses"], [])
        self.assertEqual(len(payload["daily_trend"]), 30)


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest import mock

from finance_tracker import feishu_report


class FeishuReportTest(unittest.TestCase):
    @mock.patch.object(feishu_report, "get_month_summary")
    @mock.patch.object(feishu_report, "get_today_summary")
    def test_mobile_card_is_compact_and_contains_daily_details(
        self,
        today_summary,
        month_summary,
    ):
        today_summary.return_value = {
            "date": "2026-06-15",
            "income": 100.0,
            "expense": 35.0,
            "count": 2,
            "transactions": [
                {
                    "description": "午饭",
                    "type": "支出",
                    "amount": 25.0,
                },
                {
                    "description": "地铁",
                    "type": "支出",
                    "amount": 10.0,
                },
            ],
        }
        month_summary.return_value = {
            "income": 1000.0,
            "expense": 320.0,
            "balance": 680.0,
            "budget_usage": 32.0,
            "top_categories": [
                {"category": "餐饮", "amount": 180.0},
                {"category": "交通", "amount": 80.0},
            ],
        }
        card = feishu_report.build_daily_report_card()
        content = "\n".join(
            item.get("content", "")
            for item in card["elements"]
            if item.get("tag") == "markdown"
        )
        self.assertFalse(card["config"]["wide_screen_mode"])
        self.assertIn("今日", content)
        self.assertIn("本月概览", content)
        self.assertIn("本月支出前三", content)
        self.assertIn("今日明细", content)
        self.assertIn("午饭", content)
        self.assertNotIn("| ---", content)


if __name__ == "__main__":
    unittest.main()

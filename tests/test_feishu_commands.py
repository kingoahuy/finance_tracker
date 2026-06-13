import unittest
from unittest import mock

from finance_tracker import feishu_commands


class FeishuCommandsTest(unittest.TestCase):
    def test_help(self):
        result = feishu_commands.route_command("帮助")
        self.assertEqual(result["action"], "help")
        self.assertIn("今日账单", result["text"])

    @mock.patch.object(feishu_commands, "get_today_summary")
    def test_today(self, summary):
        summary.return_value = {
            "date": "2026-06-13",
            "income": 100,
            "expense": 25,
            "balance": 75,
            "count": 2,
            "transactions": [],
        }
        result = feishu_commands.route_command("今日账单")
        self.assertIn("¥25.00", result["text"])

    @mock.patch.object(feishu_commands, "get_month_summary")
    def test_month(self, summary):
        summary.return_value = {
            "month": "2026-06",
            "income": 1000,
            "expense": 200,
            "balance": 800,
            "budget_usage": 10,
            "top_categories": [{"category": "餐饮", "amount": 100}],
        }
        result = feishu_commands.route_command("本月账单")
        self.assertIn("预算使用率：10.0%", result["text"])
        self.assertIn("餐饮", result["text"])

    @mock.patch.object(feishu_commands, "get_recent_transactions")
    def test_recent_n(self, recent):
        recent.return_value = [
            {"date": "2026-06-13", "type": "支出", "amount": 25, "category": "餐饮", "description": "午饭"}
        ]
        result = feishu_commands.route_command("最近8笔")
        recent.assert_called_once_with(8, sender_open_id=None, chat_id=None)
        self.assertIn("午饭", result["text"])

    @mock.patch.object(feishu_commands, "queue_action")
    def test_natural_language_entry_requires_confirmation(self, queue):
        queue.return_value = {
            "action_id": "action-1",
            "intent": "create_transactions",
            "payload": {
                "intent": "create_transactions",
                "transactions": [
                    {
                        "date": "2026-06-13",
                        "type": "支出",
                        "amount": 25,
                        "category": "餐饮",
                        "description": "午饭",
                    }
                ],
            },
        }
        parser = lambda text: queue.return_value["payload"]
        result = feishu_commands.route_command(
            "午饭25",
            {"message_id": "msg-1", "sender_open_id": "open-1", "chat_id": "chat-1"},
            parser=parser,
        )
        self.assertIn("确认", result["text"])
        self.assertEqual(result["pending_action_id"], "action-1")
        self.assertIn("card", result)


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest import mock

from finance_tracker import feishu_commands


class FeishuCommandsTest(unittest.TestCase):
    def _analysis_patches(self):
        return (
            mock.patch.object(
                feishu_commands,
                "get_finance_overview",
                return_value={
                    "total_income": 1000.0,
                    "total_expense": 320.0,
                    "net_income": 680.0,
                },
            ),
            mock.patch.object(
                feishu_commands,
                "get_category_expense_summary",
                return_value=[
                    {
                        "category": "餐饮",
                        "amount": 180.0,
                        "share": 56.25,
                    },
                    {
                        "category": "交通",
                        "amount": 80.0,
                        "share": 25.0,
                    },
                ],
            ),
            mock.patch.object(
                feishu_commands,
                "get_tag_summary",
                return_value=[
                    {"tag": "食堂", "amount": 120.0, "count": 6},
                    {"tag": "通勤", "amount": 60.0, "count": 10},
                ],
            ),
            mock.patch.object(
                feishu_commands,
                "get_budget_warning",
                return_value=[
                    {
                        "category": "餐饮",
                        "status": "接近超支",
                        "usage_rate": 90.0,
                    }
                ],
            ),
            mock.patch.object(
                feishu_commands,
                "generate_finance_insights",
                return_value={"saving_advice": "减少非必要餐饮支出。"},
            ),
        )

    def test_confirmation_card_uses_legacy_interactive_structure(self):
        card = feishu_commands.confirmation_card(
            {
                "action_id": "action-1",
                "intent": "create_transactions",
                "payload": {
                    "intent": "create_transactions",
                    "transactions": [
                        {
                            "date": "2026-06-14",
                            "type": "支出",
                            "category": "餐饮",
                            "amount": 10.4,
                            "description": "食堂吃饭",
                        }
                    ],
                },
            }
        )
        self.assertNotIn("schema", card)
        self.assertNotIn("body", card)
        self.assertIn("elements", card)
        action = next(
            item for item in card["elements"]
            if item.get("tag") == "action"
        )
        self.assertEqual(len(action["actions"]), 2)
        self.assertEqual(
            action["actions"][0]["value"]["operation"],
            "confirm",
        )
        self.assertIn("¥10.40", card["elements"][0]["content"])
        self.assertIn("餐饮", card["elements"][0]["content"])

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

    def test_sync_dashboard_returns_status_card(self):
        sync_result = {
            "success": True,
            "succeeded": 3,
            "failed": 0,
            "message": "同步完成",
        }
        dashboard = {
            "success": True,
            "configuration": {
                "bot_ready": True,
                "bitable_ready": True,
            },
            "fields": {
                "success": True,
                "code": 0,
                "message": "字段检查通过",
                "log_id": "log-fields",
                "missing_fields": [],
            },
            "counts": {
                "total": 675,
                "synced": 675,
                "pending": 0,
                "failed": 0,
                "record_id": 675,
            },
            "recent_errors": [],
        }
        result = feishu_commands.route_command(
            "同步状态",
            sync_callback=lambda: sync_result,
            sync_dashboard_callback=lambda: dashboard,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "sync")
        self.assertIn("card", result)
        content = result["card"]["body"]["elements"][0]["content"]
        self.assertIn("本地总流水：** 675", content)
        self.assertIn("本次同步：** 成功 3，失败 0", content)
        self.assertIn("Streamlit", content)

    def test_month_finance_analysis_returns_income_expense_balance(self):
        patches = self._analysis_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = feishu_commands.route_command("本月财务分析")
        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "finance_analysis")
        self.assertIn("收入：¥1000.00", result["text"])
        self.assertIn("支出：¥320.00", result["text"])
        self.assertIn("结余：¥680.00", result["text"])
        self.assertIn("建议：减少非必要餐饮支出。", result["text"])

    def test_top_category_query_returns_rank(self):
        patches = self._analysis_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = feishu_commands.route_command(
                "这个月哪些地方花得最多"
            )
        self.assertEqual(result["action"], "category_rank")
        self.assertIn("最大支出分类：餐饮", result["text"])
        self.assertIn("1. 餐饮 ¥180.00", result["text"])
        self.assertIn("2. 交通 ¥80.00", result["text"])

    def test_tag_analysis_returns_tag_summary(self):
        patches = self._analysis_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = feishu_commands.route_command("本月标签分析")
        self.assertEqual(result["action"], "tag_analysis")
        self.assertIn("Top 5 标签消费场景", result["text"])
        self.assertIn("食堂 ¥120.00", result["text"])
        self.assertIn("通勤 ¥60.00", result["text"])

    def test_ai_intent_is_executed_by_local_analysis(self):
        patches = self._analysis_patches()
        parser = lambda text, context=None: {
            "intent": "query_budget_analysis",
            "confidence": 0.95,
            "transactions": [],
            "query": {"period": "month", "limit": 5},
            "requires_confirmation": False,
        }
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = feishu_commands.route_command(
                "帮我分析一下预算有没有问题",
                parser=parser,
            )
        self.assertEqual(result["action"], "budget_analysis")
        self.assertIn("预算预警", result["text"])
        self.assertIn("餐饮：接近超支", result["text"])

    def test_finance_dashboard_sync_command_is_removed(self):
        sync_callback = mock.Mock(
            return_value={"success": True, "message": "不应被调用"}
        )
        result = feishu_commands.route_command(
            "同步财务看板",
            sync_callback=sync_callback,
            parser=lambda text, context=None: {
                "intent": "unknown",
                "confidence": 0,
                "transactions": [],
                "query": {},
                "requires_confirmation": False,
            },
        )
        sync_callback.assert_not_called()
        self.assertNotEqual(result["action"], "finance_dashboard_sync")

    @mock.patch.object(feishu_commands, "build_daily_report_card")
    @mock.patch.object(feishu_commands, "build_daily_report_text")
    def test_daily_report_uses_mobile_card(self, report_text, report_card):
        report_text.return_value = "手机端日报摘要"
        report_card.return_value = {"header": {}, "elements": []}
        result = feishu_commands.route_command("生成日报")
        self.assertEqual(result["action"], "report")
        self.assertEqual(result["text"], "手机端日报摘要")
        self.assertEqual(result["card"], report_card.return_value)
        report_text.assert_called_once_with()
        report_card.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

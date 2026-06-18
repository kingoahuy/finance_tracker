import datetime
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finance_tracker import (
    ai_parser,
    feishu_commands,
    ledger,
    transaction_service,
)


LOCAL_CONFIG = {
    "enabled": False,
    "require_confirmation": True,
    "fallback_to_local": True,
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "timeout": 15,
}


class FeishuConversationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = ledger.DB_FILE
        ledger.DB_FILE = Path(self.temp_dir.name) / "test.db"
        ledger.init_db()
        self.context = {
            "message_id": "msg-1",
            "sender_open_id": "open-private",
            "chat_id": "chat-private",
        }
        self.sync_patch = mock.patch.object(
            transaction_service,
            "_try_sync",
        )
        self.sync_patch.start()

    def tearDown(self):
        self.sync_patch.stop()
        ledger.DB_FILE = self.original_db
        self.temp_dir.cleanup()

    def parser(self, text, context=None):
        return ai_parser.parse_action(
            text,
            default_date="2026-06-14",
            config=LOCAL_CONFIG,
            context=context,
        )

    def route(self, text, message_id=None):
        context = dict(self.context)
        if message_id:
            context["message_id"] = message_id
        return feishu_commands.route_command(
            text,
            context,
            parser=self.parser,
        )

    def pending_action_count(self):
        with ledger.connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM pending_actions").fetchone()[0]

    def test_complete_transaction_requires_confirmation(self):
        result = self.route("午饭 28")
        self.assertTrue(result["success"])
        self.assertIn("是否确认", result["text"])
        self.assertIn("pending_action_id", result)
        self.assertTrue(ledger.load_transactions().empty)

    def test_missing_amount_then_number_creates_pending_action(self):
        question = self.route("今天吃饭了")
        self.assertIn("金额是多少", question["text"])
        pending = self.route("32", "msg-2")
        self.assertIn("¥32.00", pending["text"])
        self.assertTrue(ledger.load_transactions().empty)

    def test_revision_changes_pending_amount(self):
        first = self.route("午饭 28")
        revised = self.route("不是 28，是 32", "msg-2")
        self.assertTrue(revised["success"])
        action = ledger.get_pending_action(
            first["pending_action_id"]
        )
        self.assertEqual(
            action["payload"]["transactions"][0]["amount"],
            32,
        )
        self.assertIn("再次确认", revised["text"])

    def test_text_confirmation_variants_execute(self):
        for index, word in enumerate(("确认", "可以", "记上"), start=1):
            self.route(f"咖啡 {10 + index}", f"create-{index}")
            result = self.route(word, f"confirm-{index}")
            self.assertTrue(result["success"])
            self.assertIn("今日累计支出", result["text"])
        self.assertEqual(len(ledger.load_transactions()), 3)

    def test_text_cancel_variants_do_not_execute(self):
        for index, word in enumerate(("取消", "算了"), start=1):
            self.route(f"咖啡 {10 + index}", f"create-{index}")
            result = self.route(word, f"cancel-{index}")
            self.assertTrue(result["success"])
            self.assertIn("已取消", result["text"])
        self.assertTrue(ledger.load_transactions().empty)

    def test_text_confirm_finds_latest_valid_pending_without_session(self):
        self.route("午饭 28", "create-without-session")
        with ledger.connect() as conn:
            conn.execute("DELETE FROM feishu_sessions")
        result = self.route("确认", "confirm-without-session")
        self.assertTrue(result["success"])
        self.assertEqual(len(ledger.load_transactions()), 1)

    def test_text_cancel_finds_latest_valid_pending_without_session(self):
        self.route("午饭 28", "cancel-create-without-session")
        with ledger.connect() as conn:
            conn.execute("DELETE FROM feishu_sessions")
        result = self.route("取消", "cancel-without-session")
        self.assertTrue(result["success"])
        self.assertTrue(ledger.load_transactions().empty)

    def test_continuous_transaction(self):
        result = self.route("再加一笔咖啡 16")
        self.assertIn("餐饮支出", result["text"])
        self.assertIn("¥16.00", result["text"])

    def test_today_and_month_queries(self):
        transaction_service.create_transaction(
            {
                "date": "2026-06-14",
                "type": "支出",
                "category": "餐饮",
                "amount": 28,
                "description": "午饭",
            },
            auto_sync=False,
        )
        with mock.patch.object(
            feishu_commands,
            "get_today_summary",
            return_value={
                "date": "2026-06-14",
                "income": 0,
                "expense": 28,
                "balance": -28,
                "count": 1,
                "transactions": [],
            },
        ):
            today = self.route("我今天花了多少钱？")
        self.assertIn("支出：¥28.00", today["text"])

        with mock.patch.object(
            feishu_commands,
            "get_month_summary",
            return_value={
                "month": "2026-06",
                "income": 0,
                "expense": 28,
                "balance": -28,
                "budget": 2000,
                "budget_usage": 1.4,
                "count": 1,
                "top_categories": [
                    {"category": "餐饮", "amount": 28}
                ],
            },
        ):
            month = self.route("我这个月支出了多少？")
        self.assertIn("本月支出：¥28.00", month["text"])

    def test_delete_last_requires_confirmation(self):
        transaction_service.create_transaction(
            {
                "date": "2026-06-14",
                "type": "支出",
                "category": "餐饮",
                "amount": 28,
                "description": "午饭",
            },
            source="feishu",
            source_user_open_id=self.context["sender_open_id"],
            source_chat_id=self.context["chat_id"],
            auto_sync=False,
        )
        result = self.route("刚才那笔删掉")
        self.assertIn("是否确认", result["text"])
        self.assertIn("午饭", str(result["card"]))
        self.assertEqual(len(ledger.load_transactions()), 1)

    def test_session_stores_hashes_and_summary_only(self):
        self.route("今天吃饭了")
        session = ledger.get_feishu_session(
            self.context["sender_open_id"],
            self.context["chat_id"],
        )
        self.assertNotEqual(
            session["sender_open_id_hash"],
            self.context["sender_open_id"],
        )
        self.assertNotEqual(
            session["chat_id_hash"],
            self.context["chat_id"],
        )
        self.assertNotIn(
            self.context["sender_open_id"],
            str(session["short_history"]),
        )

    def test_monthly_bill_report_returns_markdown_without_pending_action(self):
        with mock.patch.object(
            feishu_commands,
            "call_deepseek_report",
            return_value="# Mock 月账单\n\nDeepSeek 分析",
        ) as report_call:
            result = self.route("本月账单")

        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "monthly_bill_report")
        self.assertEqual(result["text"], "# Mock 月账单\n\nDeepSeek 分析")
        self.assertNotIn("pending_action_id", result)
        self.assertEqual(self.pending_action_count(), 0)
        report_call.assert_called_once()
        self.assertEqual(report_call.call_args.args[0], "monthly_bill")

    def test_monthly_bill_alias_returns_markdown(self):
        with mock.patch.object(
            feishu_commands,
            "call_deepseek_report",
            return_value="# Mock 月账单",
        ):
            result = self.route("这个月花了多少")

        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "monthly_bill_report")
        self.assertEqual(result["text"], "# Mock 月账单")
        self.assertEqual(self.pending_action_count(), 0)

    def test_daily_report_dates_are_passed_to_payload_builder(self):
        seen_dates = []

        def fake_daily_payload(target_date):
            seen_dates.append(target_date)
            return {"report_type": "daily_report", "date": str(target_date)}

        with mock.patch.object(
            feishu_commands,
            "build_daily_report_payload",
            side_effect=fake_daily_payload,
        ), mock.patch.object(
            feishu_commands,
            "call_deepseek_report",
            return_value="# Mock 日报",
        ):
            today_result = self.route("记账日报", "report-today")
            yesterday_result = self.route("生成昨天日报", "report-yesterday")
            explicit_result = self.route("生成 2026-06-14 日报", "report-explicit")
            chinese_result = self.route("2026年6月14日的日报", "report-chinese")

        self.assertEqual(today_result["action"], "daily_report")
        self.assertEqual(yesterday_result["action"], "daily_report")
        self.assertEqual(explicit_result["action"], "daily_report")
        self.assertEqual(chinese_result["action"], "daily_report")
        self.assertEqual(str(seen_dates[0]), str(datetime.date.today()))
        self.assertEqual(
            str(seen_dates[1]),
            str(datetime.date.today() - datetime.timedelta(days=1)),
        )
        self.assertEqual(str(seen_dates[2]), "2026-06-14")
        self.assertEqual(str(seen_dates[3]), "2026-06-14")
        self.assertEqual(self.pending_action_count(), 0)

    def test_tag_and_consumption_reports_return_markdown_without_pending_action(self):
        with mock.patch.object(
            feishu_commands,
            "call_deepseek_report",
            return_value="# Mock Report",
        ) as report_call:
            tag_result = self.route("本月标签分析", "tag-report")
            consumption_result = self.route("本月消费报告", "consumption-report")

        self.assertEqual(tag_result["action"], "monthly_tag_analysis")
        self.assertEqual(consumption_result["action"], "monthly_consumption_report")
        self.assertEqual(tag_result["text"], "# Mock Report")
        self.assertEqual(consumption_result["text"], "# Mock Report")
        self.assertEqual(self.pending_action_count(), 0)
        prompt_names = [call.args[0] for call in report_call.call_args_list]
        self.assertIn("monthly_tag_analysis", prompt_names)
        self.assertIn("monthly_consumption_report", prompt_names)

    def test_deepseek_failure_returns_fallback_markdown(self):
        with mock.patch.object(
            feishu_commands,
            "call_deepseek_report",
            side_effect=TimeoutError("timeout"),
        ):
            result = self.route("本月账单")

        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "monthly_bill_report")
        self.assertIn("# 本月账单", result["text"])
        self.assertEqual(self.pending_action_count(), 0)

    def test_prompt_library_does_not_restore_dashboard_sync_entry(self):
        text = feishu_commands.HELP_TEXT
        self.assertNotIn("同步财务看板", text)
        self.assertNotIn("更新财务看板", text)


if __name__ == "__main__":
    unittest.main()

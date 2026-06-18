import json
import unittest
from types import SimpleNamespace
from unittest import mock

from finance_tracker import ai_parser


class FakeCompletions:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        message = SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class AiParserTest(unittest.TestCase):
    def _client(self, completions):
        return SimpleNamespace(chat=SimpleNamespace(completions=completions))

    def test_valid_ai_json_is_normalized(self):
        completions = FakeCompletions(
            {
                "intent": "create_transactions",
                "confidence": 0.92,
                "transactions": [
                    {
                        "date": "2026-06-13",
                        "type": "支出",
                        "category": "餐饮",
                        "amount": 25,
                        "description": "午饭",
                    }
                ],
            }
        )
        result = ai_parser.parse_action(
            "午饭25",
            client=self._client(completions),
            config={
                "enabled": True,
                "require_confirmation": True,
                "fallback_to_local": True,
                "api_key": "test",
                "base_url": "https://example.invalid",
                "model": "test-model",
                "timeout": 3,
            },
        )
        self.assertEqual(result["intent"], "create_transactions")
        self.assertEqual(result["parser"], "ai")
        self.assertTrue(result["need_confirmation"])
        self.assertEqual(completions.kwargs["response_format"], {"type": "json_object"})

    def test_ai_tags_are_preserved_and_supplemented(self):
        completions = FakeCompletions(
            {
                "intent": "create_transactions",
                "confidence": 0.95,
                "transactions": [
                    {
                        "date": "2026-06-15",
                        "type": "支出",
                        "category": "餐饮",
                        "amount": 16,
                        "description": "咖啡",
                        "tags": ["AI自定义"],
                        "is_need": False,
                        "is_fixed": False,
                    }
                ],
                "requires_confirmation": True,
            }
        )
        result = ai_parser.parse_action(
            "咖啡16",
            client=self._client(completions),
            config={
                "enabled": True,
                "require_confirmation": True,
                "fallback_to_local": True,
                "api_key": "test",
                "base_url": "https://example.invalid",
                "model": "test-model",
                "timeout": 3,
            },
        )
        tags = result["transactions"][0]["tags"].split(",")
        self.assertEqual(tags[0], "AI自定义")
        self.assertIn("咖啡", tags)

    def test_timeout_falls_back_to_local_without_logging_text(self):
        completions = FakeCompletions(error=TimeoutError("secret lunch text"))
        with self.assertLogs("finance_tracker.ai_parser", level="INFO") as logs:
            result = ai_parser.parse_action(
                "午饭25",
                client=self._client(completions),
                config={
                    "enabled": True,
                    "require_confirmation": True,
                    "fallback_to_local": True,
                    "api_key": "test",
                    "base_url": "https://example.invalid",
                    "model": "test-model",
                    "timeout": 1,
                },
            )
        self.assertEqual(result["parser"], "local")
        self.assertNotIn("午饭25", "\n".join(logs.output))
        self.assertNotIn("secret lunch text", "\n".join(logs.output))

    def test_low_confidence_falls_back_to_local(self):
        completions = FakeCompletions(
            {"intent": "unknown", "confidence": 0.2, "transactions": []}
        )
        result = ai_parser.parse_action(
            "午饭25",
            client=self._client(completions),
            config={
                "enabled": True,
                "require_confirmation": True,
                "fallback_to_local": True,
                "api_key": "test",
                "base_url": "https://example.invalid",
                "model": "test-model",
                "timeout": 3,
            },
        )
        self.assertEqual(result["parser"], "local")

    def test_unnecessary_ai_amount_question_falls_back_to_local(self):
        completions = FakeCompletions(
            {
                "intent": "ask_clarification",
                "confidence": 0.99,
                "clarification_question": "金额是多少？",
                "transactions": [],
            }
        )
        result = ai_parser.parse_action(
            "午饭28",
            client=self._client(completions),
            config={
                "enabled": True,
                "require_confirmation": True,
                "fallback_to_local": True,
                "api_key": "test",
                "base_url": "https://example.invalid",
                "model": "test-model",
                "timeout": 3,
            },
        )
        self.assertEqual(result["parser"], "local")
        self.assertEqual(result["intent"], "create_transactions")
        self.assertEqual(result["transactions"][0]["amount"], 28)

    def test_local_update_is_not_misread_as_new_transaction(self):
        result = ai_parser.parse_action(
            "把上一笔金额改成30",
            config={
                "enabled": False,
                "require_confirmation": True,
                "fallback_to_local": True,
                "api_key": "",
                "base_url": "",
                "model": "",
                "timeout": 1,
            },
        )
        self.assertEqual(result["intent"], "update_last_transaction")
        self.assertEqual(result["updates"]["amount"], 30)
        self.assertEqual(result["transactions"], [])

    def test_ai_finance_analysis_intent_is_accepted_without_confirmation(self):
        completions = FakeCompletions(
            {
                "intent": "query_finance_analysis",
                "confidence": 0.95,
                "transactions": [],
                "requires_confirmation": False,
            }
        )
        result = ai_parser.parse_action(
            "帮我深入看看这个月的消费结构",
            client=self._client(completions),
            config={
                "enabled": True,
                "require_confirmation": True,
                "fallback_to_local": True,
                "api_key": "test",
                "base_url": "https://example.invalid",
                "model": "test-model",
                "timeout": 3,
            },
        )
        self.assertEqual(result["intent"], "query_finance_analysis")
        self.assertFalse(result["need_confirmation"])

    def test_ai_detail_sync_intent_is_accepted(self):
        action = ai_parser.validate_action(
            {
                "intent": "sync_bitable",
                "confidence": 0.95,
                "transactions": [],
                "requires_confirmation": False,
            },
            "2026-06-15",
        )
        self.assertEqual(action["intent"], "sync_bitable")
        self.assertFalse(action["requires_confirmation"])

    def test_new_report_and_sync_status_intents_are_accepted(self):
        for intent in (
            "help",
            "query_today_summary",
            "query_month_summary",
            "query_recent_transactions",
            "query_category_summary",
            "monthly_bill_report",
            "daily_report",
            "monthly_tag_analysis",
            "monthly_consumption_report",
            "generate_daily_report",
            "generate_monthly_report",
            "generate_yearly_report",
            "sync_bitable",
            "sync_status",
        ):
            action = ai_parser.validate_action(
                {
                    "intent": intent,
                    "confidence": 0.95,
                    "transactions": [],
                    "requires_confirmation": False,
                    "query": {
                        "date": "2026-06-14",
                        "month": "2026-06",
                        "year": "2026",
                        "limit": 5,
                    },
                },
                "2026-06-15",
            )
            self.assertEqual(action["intent"], intent)
            self.assertFalse(action["requires_confirmation"])

    def test_local_report_commands_are_recognized(self):
        config = {
            "enabled": False,
            "require_confirmation": True,
            "fallback_to_local": True,
            "api_key": "",
            "base_url": "",
            "model": "",
            "timeout": 1,
        }
        cases = {
            "本月账单": "monthly_bill_report",
            "这个月花了多少": "monthly_bill_report",
            "生成今日日报": "daily_report",
            "生成昨天日报": "daily_report",
            "生成 2026-06-14 日报": "daily_report",
            "2026年6月14日的日报": "daily_report",
            "本月标签分析": "monthly_tag_analysis",
            "这个月钱花在哪些场景": "monthly_tag_analysis",
            "本月消费报告": "monthly_consumption_report",
            "这个月消费怎么样": "monthly_consumption_report",
            "生成本月月报": "generate_monthly_report",
            "生成 2026-06 月报": "generate_monthly_report",
            "生成今年年报": "generate_yearly_report",
            "生成 2026 年报": "generate_yearly_report",
            "检查同步": "sync_status",
            "同步到飞书多维表格": "sync_bitable",
        }
        for text, intent in cases.items():
            result = ai_parser.parse_action(
                text,
                default_date="2026-06-15",
                config=config,
            )
            self.assertEqual(result["intent"], intent)
            self.assertFalse(result["need_confirmation"])

    def test_local_daily_report_dates_are_recognized(self):
        config = {
            "enabled": False,
            "require_confirmation": True,
            "fallback_to_local": True,
            "api_key": "",
            "base_url": "",
            "model": "",
            "timeout": 1,
        }
        cases = {
            "记账日报": "2026-06-15",
            "生成昨天日报": "2026-06-14",
            "生成 2026-06-14 日报": "2026-06-14",
            "2026年6月14日的日报": "2026-06-14",
        }
        for text, expected_date in cases.items():
            result = ai_parser.parse_action(
                text,
                default_date="2026-06-15",
                config=config,
            )
            self.assertEqual(result["intent"], "daily_report")
            self.assertEqual(result["query"]["date"], expected_date)
            self.assertFalse(result["need_confirmation"])

    def test_removed_dashboard_sync_is_not_detail_sync(self):
        result = ai_parser.parse_action(
            "同步财务看板",
            config={
                "enabled": False,
                "require_confirmation": True,
                "fallback_to_local": True,
                "api_key": "",
                "base_url": "",
                "model": "",
                "timeout": 1,
            },
        )
        self.assertNotEqual(result["intent"], "sync_bitable")


if __name__ == "__main__":
    unittest.main()

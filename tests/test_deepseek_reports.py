import unittest
from unittest import mock

from finance_tracker import deepseek_reports


class _Message:
    content = "# DeepSeek Markdown\n\n分析完成。"


class _Choice:
    message = _Message()


class _Response:
    choices = [_Choice()]


class _Completions:
    def create(self, **kwargs):
        return _Response()


class _OpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.chat = type("Chat", (), {"completions": _Completions()})()


class _FailingCompletions:
    def create(self, **kwargs):
        raise TimeoutError("timeout")


class _FailingOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.chat = type("Chat", (), {"completions": _FailingCompletions()})()


class DeepSeekReportsTest(unittest.TestCase):
    def _payload(self):
        return {
            "report_type": "monthly_bill",
            "month": "2026-06",
            "currency": "CNY",
            "overview": {
                "income": 1000.0,
                "expense": 200.0,
                "balance": 800.0,
                "transaction_count": 2,
            },
            "budget": {
                "monthly_budget": 2000.0,
                "used": 200.0,
                "usage_rate": 10.0,
                "status": "正常",
            },
            "top_categories": [{"category": "餐饮", "amount": 200.0, "count": 2}],
            "top_tags": [{"tag": "午餐", "amount": 200.0, "count": 2}],
            "top_expenses": [
                {
                    "date": "2026-06-14",
                    "category": "餐饮",
                    "amount": 100.0,
                    "description": "午餐",
                }
            ],
        }

    def test_prompts_include_required_constraints(self):
        for prompt in [
            deepseek_reports.MONTHLY_BILL_PROMPT,
            deepseek_reports.DAILY_REPORT_PROMPT,
            deepseek_reports.MONTHLY_TAG_ANALYSIS_PROMPT,
            deepseek_reports.MONTHLY_CONSUMPTION_REPORT_PROMPT,
        ]:
            self.assertIn("只能使用 data_payload", prompt)
            self.assertIn("不得编造", prompt)
            self.assertIn("Markdown", prompt)

    @mock.patch.dict(
        "os.environ",
        {
            "DEEPSEEK_API_KEY": "sk-test-secret",
            "DEEPSEEK_BASE_URL": "https://example.test",
            "DEEPSEEK_MODEL": "deepseek-test",
            "AI_PARSER_TIMEOUT_SECONDS": "3",
        },
    )
    def test_call_deepseek_report_success_returns_markdown(self):
        with mock.patch.object(deepseek_reports, "OpenAI", _OpenAI):
            result = deepseek_reports.call_deepseek_report(
                "monthly_bill",
                self._payload(),
            )

        self.assertEqual(result, "# DeepSeek Markdown\n\n分析完成。")

    @mock.patch.dict(
        "os.environ",
        {
            "DEEPSEEK_API_KEY": "sk-test-secret",
            "DEEPSEEK_BASE_URL": "https://example.test",
            "DEEPSEEK_MODEL": "deepseek-test",
            "AI_PARSER_TIMEOUT_SECONDS": "3",
        },
    )
    def test_call_deepseek_report_failure_returns_fallback_markdown(self):
        with mock.patch.object(deepseek_reports, "OpenAI", _FailingOpenAI):
            result = deepseek_reports.call_deepseek_report(
                "monthly_bill",
                self._payload(),
            )

        self.assertIn("# 本月账单 2026-06", result)
        self.assertIn("DeepSeek 暂不可用", result)

    @mock.patch.dict(
        "os.environ",
        {
            "DEEPSEEK_API_KEY": "sk-test-secret",
            "DEEPSEEK_BASE_URL": "https://example.test",
            "DEEPSEEK_MODEL": "deepseek-test",
            "AI_PARSER_TIMEOUT_SECONDS": "3",
        },
    )
    def test_logs_do_not_output_api_key_or_access_token(self):
        with mock.patch.object(deepseek_reports, "OpenAI", _FailingOpenAI):
            with self.assertLogs("finance_tracker.deepseek_reports", level="WARNING") as logs:
                deepseek_reports.call_deepseek_report("monthly_bill", self._payload())

        text = "\n".join(logs.output)
        self.assertNotIn("sk-test-secret", text)
        self.assertNotIn("access_token", text)


if __name__ == "__main__":
    unittest.main()

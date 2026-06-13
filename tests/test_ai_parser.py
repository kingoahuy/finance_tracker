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


if __name__ == "__main__":
    unittest.main()

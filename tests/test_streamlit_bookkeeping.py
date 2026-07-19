import unittest

from finance_tracker import streamlit_bookkeeping


class StreamlitBookkeepingTest(unittest.TestCase):
    def test_web_parser_requires_ai_and_returns_confirmation_draft(self):
        calls = {}

        def fake_parser(text, **kwargs):
            calls.update(kwargs)
            return {
                "intent": "create_transactions",
                "parser": "ai",
                "model_tier": "pro",
                "transactions": [
                    {
                        "date": "2026-07-18",
                        "type": "支出",
                        "category": "餐饮",
                        "amount": 20,
                        "description": "午饭",
                        "tags": "正餐",
                    }
                ],
            }

        result = streamlit_bookkeeping.parse_web_bookkeeping(
            "午饭20", parser=fake_parser
        )

        self.assertTrue(result["success"])
        self.assertTrue(calls["ai_only"])
        self.assertEqual(calls["context"], {"task_mode": "bookkeeping_only"})
        self.assertEqual(result["action"]["model_tier"], "pro")

    def test_confirmed_ai_draft_is_written_as_streamlit_deepseek(self):
        captured = {}

        def fake_writer(records, **kwargs):
            captured["records"] = records
            captured.update(kwargs)
            return records

        action = {
            "intent": "create_transactions",
            "parser": "ai",
            "transactions": [{"amount": 20}],
        }
        result = streamlit_bookkeeping.commit_web_bookkeeping(
            action, writer=fake_writer
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(captured["source"], "streamlit_deepseek")
        self.assertTrue(captured["auto_sync"])

    def test_local_draft_cannot_be_committed(self):
        with self.assertRaises(ValueError):
            streamlit_bookkeeping.commit_web_bookkeeping(
                {
                    "intent": "create_transactions",
                    "parser": "local",
                    "transactions": [{"amount": 20}],
                },
                writer=lambda *_args, **_kwargs: [],
            )


if __name__ == "__main__":
    unittest.main()

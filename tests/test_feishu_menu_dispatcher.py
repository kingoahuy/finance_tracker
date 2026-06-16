import unittest
from types import SimpleNamespace
from unittest import mock

from finance_tracker import feishu_bot, feishu_menu_dispatcher
from finance_tracker.feishu_config import FeishuConfig


class FakeMenuClient:
    def __init__(self):
        self.messages = []

    def send_text(self, receive_id, text, receive_id_type="chat_id"):
        self.messages.append(
            {
                "receive_id": receive_id,
                "text": text,
                "receive_id_type": receive_id_type,
            }
        )
        return {
            "success": True,
            "code": 0,
            "message": "",
            "log_id": "menu-test",
        }

    def send_card(self, receive_id, card, receive_id_type="chat_id"):
        self.messages.append(
            {
                "receive_id": receive_id,
                "card": card,
                "receive_id_type": receive_id_type,
            }
        )
        return {
            "success": True,
            "code": 0,
            "message": "",
            "log_id": "menu-card-test",
        }


def menu_event(event_key="today_bill", open_id="open-1"):
    return SimpleNamespace(
        header=SimpleNamespace(
            event_type="application.bot.menu_v6",
            event_id="menu-event-1",
        ),
        event=SimpleNamespace(
            event_key=event_key,
            operator=SimpleNamespace(
                operator_id=SimpleNamespace(open_id=open_id)
            ),
        ),
    )


def menu_config():
    return FeishuConfig(
        app_id="app",
        app_secret="secret",
        verification_token="",
        encrypt_key="",
        allowed_open_ids=("open-1",),
        allowed_chat_ids=(),
        bootstrap_mode=False,
        bitable_app_token="",
        bitable_table_id="",
        bot_enabled=True,
        bitable_sync_enabled=False,
        auto_sync=False,
        daily_report_enabled=False,
        daily_report_time="21:30",
        log_level="INFO",
        sync_retry_limit=5,
    )


class FeishuMenuDispatcherTest(unittest.TestCase):
    def test_all_required_menu_keys_use_mapping_dispatch(self):
        self.assertEqual(
            set(feishu_menu_dispatcher.MENU_HANDLERS),
            {
                "today_bill",
                "month_summary",
                "category_rank",
                "budget_warning",
                "daily_report",
                "sync_refresh",
                "help",
            },
        )

    @mock.patch.object(feishu_menu_dispatcher, "get_finance_overview")
    def test_today_bill_reuses_finance_overview(self, overview):
        overview.return_value = {
            "total_income": 100.0,
            "total_expense": 25.0,
            "net_income": 75.0,
            "transaction_count": 2,
        }
        text = feishu_menu_dispatcher.handle_menu_event(
            "today_bill",
            "open-1",
        )
        self.assertIn("今日账单", text)
        self.assertIn("¥25.00", text)
        overview.assert_called_once()

    @mock.patch.object(
        feishu_menu_dispatcher,
        "get_category_expense_summary",
    )
    def test_category_rank_reuses_analytics(self, category_summary):
        category_summary.return_value = [
            {
                "category": "餐饮",
                "amount": 88.0,
                "share": 44.0,
            }
        ]
        text = feishu_menu_dispatcher.handle_menu_event(
            "category_rank",
            "open-1",
        )
        self.assertIn("分类排行", text)
        self.assertIn("餐饮", text)
        self.assertIn("44.0%", text)

    @mock.patch.object(
        feishu_menu_dispatcher,
        "sync_pending_transactions",
    )
    def test_sync_refresh_reuses_existing_sync(self, sync_pending):
        sync_pending.return_value = {
            "success": True,
            "processed": 3,
            "succeeded": 3,
            "failed": 0,
        }
        text = feishu_menu_dispatcher.handle_menu_event(
            "sync_refresh",
            "open-1",
        )
        self.assertIn("同步刷新完成", text)
        self.assertIn("成功：3", text)
        sync_pending.assert_called_once_with()

    def test_unknown_menu_key_returns_help(self):
        text = feishu_menu_dispatcher.handle_menu_event(
            "unknown-key",
            "open-1",
        )
        self.assertIn("未识别", text)
        self.assertIn("今日账单", text)

    @mock.patch.object(
        feishu_bot,
        "handle_menu_event",
        return_value="菜单回复",
    )
    def test_menu_callback_extracts_open_id_and_sends_private_text(
        self,
        dispatcher,
    ):
        client = FakeMenuClient()
        response = feishu_bot.handle_menu_event_callback(
            menu_event(),
            api_client=client,
            config=menu_config(),
        )
        dispatcher.assert_called_once_with("today_bill", "open-1")
        self.assertTrue(response["success"])
        self.assertEqual(
            client.messages,
            [
                {
                    "receive_id": "open-1",
                    "text": "菜单回复",
                    "receive_id_type": "open_id",
                }
            ],
        )

    def test_unapproved_menu_user_is_rejected(self):
        client = FakeMenuClient()
        response = feishu_bot.handle_menu_event_callback(
            menu_event(open_id="open-not-allowed"),
            api_client=client,
            config=menu_config(),
        )
        self.assertIsNone(response)
        self.assertEqual(client.messages, [])

    def test_non_menu_event_type_is_ignored(self):
        client = FakeMenuClient()
        event = menu_event()
        event.header.event_type = "im.message.receive_v1"
        response = feishu_bot.handle_menu_event_callback(
            event,
            api_client=client,
            config=menu_config(),
        )
        self.assertIsNone(response)
        self.assertEqual(client.messages, [])

    @mock.patch.object(
        feishu_bot,
        "build_daily_report_card",
        return_value={"header": {}, "elements": []},
    )
    @mock.patch.object(
        feishu_bot,
        "handle_menu_event",
        return_value="日报短文本",
    )
    def test_daily_report_menu_sends_private_card(self, dispatcher, report_card):
        client = FakeMenuClient()
        response = feishu_bot.handle_menu_event_callback(
            menu_event(event_key="daily_report"),
            api_client=client,
            config=menu_config(),
        )
        self.assertTrue(response["success"])
        self.assertEqual(client.messages[0]["receive_id"], "open-1")
        self.assertEqual(client.messages[0]["receive_id_type"], "open_id")
        self.assertEqual(
            client.messages[0]["card"],
            report_card.return_value,
        )


if __name__ == "__main__":
    unittest.main()

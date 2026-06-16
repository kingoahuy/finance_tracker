import unittest
from pathlib import Path


class DashboardSettingsUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (
            Path(__file__).resolve().parents[1]
            / "finance_tracker"
            / "app.py"
        ).read_text(encoding="utf-8")

    def test_summary_dashboard_management_buttons_are_removed(self):
        for label in (
            "检查汇总表",
            "同步全部汇总",
            "同步当前月份",
            "审计汇总表",
            "生成看板搭建指南",
            "打开指南路径提示",
            "重新读取看板状态",
            "飞书财务看板",
        ):
            self.assertNotIn(label, self.source)

    def test_dashboard_functions_are_not_wired(self):
        for function_name in (
            "check_dashboard",
            "sync_dashboard_summary",
            "sync_dashboard_month",
            "audit_dashboard",
            "generate_dashboard_guide",
            "bitable_dashboard",
        ):
            self.assertNotIn(function_name, self.source)

    def test_original_detail_sync_controls_remain(self):
        for function_name in (
            "sync_pending_transactions",
            "full_sync",
            "sync_one_pending",
            "get_sync_dashboard",
        ):
            self.assertIn(function_name, self.source)

    def test_app_source_can_be_compiled_after_dashboard_removal(self):
        compile(self.source, "finance_tracker/app.py", "exec")


if __name__ == "__main__":
    unittest.main()

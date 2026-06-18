import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finance_tracker.config import EnvFileValidationError, load_env_file
from finance_tracker.feishu_config import get_feishu_config, get_feishu_config_status


class FeishuConfigTest(unittest.TestCase):
    def _env_file(self, content):
        temp_dir = tempfile.TemporaryDirectory()
        path = Path(temp_dir.name) / ".env"
        path.write_text(content, encoding="utf-8")
        self.addCleanup(temp_dir.cleanup)
        return path

    def test_empty_system_value_is_filled_from_env_file(self):
        path = self._env_file("FEISHU_ALLOWED_OPEN_IDS=ou_valid_test_id\n")
        with mock.patch.dict(
            os.environ,
            {"FEISHU_ALLOWED_OPEN_IDS": ""},
            clear=True,
        ):
            config = get_feishu_config(env_path=path)
            self.assertEqual(config.allowed_open_ids, ("ou_valid_test_id",))

    def test_nonempty_system_value_is_not_overwritten(self):
        path = self._env_file("FEISHU_ALLOWED_OPEN_IDS=ou_file_value\n")
        with mock.patch.dict(
            os.environ,
            {"FEISHU_ALLOWED_OPEN_IDS": "ou_system_value"},
            clear=True,
        ):
            config = get_feishu_config(env_path=path)
            self.assertEqual(config.allowed_open_ids, ("ou_system_value",))

    def test_duplicate_keys_are_rejected_without_values(self):
        path = self._env_file(
            "FEISHU_ALLOWED_OPEN_IDS=ou_first\n"
            "FEISHU_ALLOWED_OPEN_IDS=\n"
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(EnvFileValidationError) as context:
                load_env_file(path)
        message = str(context.exception)
        self.assertIn("FEISHU_ALLOWED_OPEN_IDS", message)
        self.assertNotIn("ou_first", message)

    def test_empty_allowlist_is_reported(self):
        path = self._env_file(
            "FEISHU_APP_ID=cli_test\n"
            "FEISHU_APP_SECRET=test_secret\n"
            "FEISHU_ALLOWED_OPEN_IDS=\n"
            "FEISHU_BOOTSTRAP_MODE=false\n"
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            status = get_feishu_config_status(env_path=path)
        self.assertEqual(status["allowed_user_count"], 0)
        self.assertTrue(status["validation_errors"])

    def test_bitable_settings_are_loaded_from_env_file(self):
        path = self._env_file(
            "FEISHU_APP_ID=cli_test\n"
            "FEISHU_APP_SECRET=test_secret\n"
            "FEISHU_BITABLE_APP_TOKEN=base_test\n"
            "FEISHU_BITABLE_TABLE_ID=tbl_test\n"
            "FEISHU_BITABLE_SYNC_ENABLED=true\n"
            "FEISHU_AUTO_SYNC=false\n"
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            config = get_feishu_config(env_path=path)
            status = get_feishu_config_status(config=config)
        self.assertEqual(config.bitable_app_token, "base_test")
        self.assertEqual(config.bitable_table_id, "tbl_test")
        self.assertTrue(config.bitable_sync_enabled)
        self.assertFalse(config.auto_sync)
        self.assertTrue(status["bitable_app_token_configured"])
        self.assertTrue(status["bitable_table_id_configured"])
        self.assertFalse(status["bitable_auto_sync"])

    def test_proactive_daily_report_is_disabled_by_default(self):
        path = self._env_file("")
        with mock.patch.dict(os.environ, {}, clear=True):
            config = get_feishu_config(env_path=path)
        self.assertFalse(config.daily_report_enabled)


if __name__ == "__main__":
    unittest.main()

import os
from dataclasses import dataclass

try:
    from .config import PROJECT_ROOT, EnvFileValidationError, load_env_file
except ImportError:
    from config import PROJECT_ROOT, EnvFileValidationError, load_env_file


def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_list(value):
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str
    app_secret: str
    verification_token: str
    encrypt_key: str
    allowed_open_ids: tuple
    allowed_chat_ids: tuple
    bootstrap_mode: bool
    bitable_app_token: str
    bitable_table_id: str
    bot_enabled: bool
    bitable_sync_enabled: bool
    auto_sync: bool
    daily_report_enabled: bool
    daily_report_time: str
    log_level: str
    sync_retry_limit: int

    @property
    def bot_ready(self):
        return bool(self.app_id and self.app_secret)

    @property
    def bitable_ready(self):
        return bool(self.bot_ready and self.bitable_app_token and self.bitable_table_id)


def get_feishu_config(env_path=None):
    load_env_file(path=env_path or PROJECT_ROOT / ".env", override=False)
    return FeishuConfig(
        app_id=os.getenv("FEISHU_APP_ID", "").strip(),
        app_secret=os.getenv("FEISHU_APP_SECRET", "").strip(),
        verification_token=os.getenv("FEISHU_VERIFICATION_TOKEN", "").strip(),
        encrypt_key=os.getenv("FEISHU_ENCRYPT_KEY", "").strip(),
        allowed_open_ids=_as_list(os.getenv("FEISHU_ALLOWED_OPEN_IDS")),
        allowed_chat_ids=_as_list(os.getenv("FEISHU_ALLOWED_CHAT_IDS")),
        bootstrap_mode=_as_bool(os.getenv("FEISHU_BOOTSTRAP_MODE"), False),
        bitable_app_token=os.getenv("FEISHU_BITABLE_APP_TOKEN", "").strip(),
        bitable_table_id=os.getenv("FEISHU_BITABLE_TABLE_ID", "").strip(),
        bot_enabled=_as_bool(os.getenv("FEISHU_BOT_ENABLED"), True),
        bitable_sync_enabled=_as_bool(os.getenv("FEISHU_BITABLE_SYNC_ENABLED"), True),
        auto_sync=_as_bool(os.getenv("FEISHU_AUTO_SYNC"), True),
        # Proactive messages must be opt-in. A missing setting must never
        # silently enable scheduled Feishu delivery.
        daily_report_enabled=_as_bool(os.getenv("FEISHU_DAILY_REPORT_ENABLED"), False),
        daily_report_time=os.getenv("FEISHU_DAILY_REPORT_TIME", "21:30").strip(),
        log_level=os.getenv("FEISHU_LOG_LEVEL", "INFO").strip().upper(),
        sync_retry_limit=max(1, int(os.getenv("FEISHU_SYNC_RETRY_LIMIT", "5"))),
    )


def get_feishu_config_status(config=None, env_path=None):
    try:
        config = config or get_feishu_config(env_path=env_path)
    except EnvFileValidationError as exc:
        return {
            "bot_enabled": False,
            "bot_ready": False,
            "bitable_enabled": False,
            "bitable_ready": False,
            "bitable_auto_sync": False,
            "bitable_app_token_configured": False,
            "bitable_table_id_configured": False,
            "missing": [],
            "bitable_missing": [],
            "bootstrap_mode": False,
            "allowed_user_count": 0,
            "allowed_chat_count": 0,
            "validation_errors": [str(exc)],
        }
    missing = []
    if not config.app_id:
        missing.append("FEISHU_APP_ID")
    if not config.app_secret:
        missing.append("FEISHU_APP_SECRET")
    bitable_missing = []
    if not config.bitable_app_token:
        bitable_missing.append("FEISHU_BITABLE_APP_TOKEN")
    if not config.bitable_table_id:
        bitable_missing.append("FEISHU_BITABLE_TABLE_ID")
    validation_errors = []
    if config.bot_enabled and not config.bootstrap_mode and not config.allowed_open_ids:
        validation_errors.append(
            "FEISHU_ALLOWED_OPEN_IDS is empty while bootstrap mode is disabled."
        )
    return {
        "bot_enabled": config.bot_enabled,
        "bot_ready": config.bot_ready,
        "bitable_enabled": config.bitable_sync_enabled,
        "bitable_ready": config.bitable_ready,
        "bitable_auto_sync": config.auto_sync,
        "bitable_app_token_configured": bool(config.bitable_app_token),
        "bitable_table_id_configured": bool(config.bitable_table_id),
        "missing": missing,
        "bitable_missing": bitable_missing,
        "bootstrap_mode": config.bootstrap_mode,
        "allowed_user_count": len(config.allowed_open_ids),
        "allowed_chat_count": len(config.allowed_chat_ids),
        "validation_errors": validation_errors,
    }

"""Conservative scheduled cleanup for provably invalid ledger rows."""

import datetime
import math
import os

try:
    from .bitable_sync import BitableSyncService, cleanup_test_records
    from .config import PROJECT_ROOT, load_env_file
    from .feishu_config import get_feishu_config
    from .ledger import connect, init_db
except ImportError:
    from bitable_sync import BitableSyncService, cleanup_test_records
    from config import PROJECT_ROOT, load_env_file
    from feishu_config import get_feishu_config
    from ledger import connect, init_db


_LAST_CLEANUP_DATE = None
VALID_TYPES = {"收入", "支出"}
VALID_STATUSES = {"active", "deleted"}


def get_cleanup_config():
    load_env_file(PROJECT_ROOT / ".env", override=False)
    return {
        "enabled": _as_bool(os.getenv("INVALID_DATA_CLEANUP_ENABLED"), True),
        "hour": _bounded_int(os.getenv("INVALID_DATA_CLEANUP_HOUR"), 3, 0, 23),
        "retention_days": _bounded_int(
            os.getenv("INVALID_DATA_RETENTION_DAYS"), 30, 7, 3650
        ),
    }


def inspect_invalid_transactions(now=None, retention_days=30):
    """Return only rows that are structurally invalid or safely past retention."""
    init_db()
    now = now or datetime.datetime.now()
    cutoff = now - datetime.timedelta(days=max(7, int(retention_days)))
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT rowid, transaction_uid, date, type, amount, status,
                   deleted_at, sync_status, feishu_record_id
            FROM transactions
            ORDER BY rowid
            """
        ).fetchall()
    planned = []
    for row in rows:
        rowid, uid, date_value, txn_type, amount, status, deleted_at, sync_status, record_id = row
        uid = str(uid or "").strip()
        status = str(status or "active").strip()
        reasons = []
        if status == "active":
            if not uid:
                reasons.append("missing_transaction_uid")
            elif uid.lower().startswith("test_"):
                reasons.append("test_transaction_uid")
            if not _valid_date(date_value):
                reasons.append("invalid_date")
            if str(txn_type or "").strip() not in VALID_TYPES:
                reasons.append("invalid_type")
            if not _positive_finite_amount(amount):
                reasons.append("invalid_amount")
        elif status == "deleted":
            deleted_time = _as_datetime(deleted_at)
            if deleted_time and deleted_time <= cutoff and str(sync_status or "") == "synced":
                reasons.append("expired_soft_delete")
        elif status not in VALID_STATUSES:
            reasons.append("invalid_status")
        if reasons:
            planned.append(
                {
                    "rowid": int(rowid),
                    "transaction_uid": uid,
                    "transaction_uid_prefix": uid[:8],
                    "feishu_record_id": str(record_id or "").strip(),
                    "reasons": reasons,
                }
            )
    return planned


def cleanup_invalid_transactions(
    apply=False,
    service=None,
    now=None,
    retention_days=None,
    include_remote_tests=True,
):
    """Delete safe candidates, deleting their Feishu rows before local rows."""
    config = get_cleanup_config()
    retention_days = int(retention_days or config["retention_days"])
    planned = inspect_invalid_transactions(now=now, retention_days=retention_days)
    result = {
        "success": True,
        "mode": "apply" if apply else "dry-run",
        "planned_delete_count": len(planned),
        "planned_deletions": [
            {
                "rowid": item["rowid"],
                "transaction_uid_prefix": item["transaction_uid_prefix"],
                "reasons": item["reasons"],
                "requires_remote_delete": bool(item["feishu_record_id"]),
            }
            for item in planned
        ],
        "local_deleted_count": 0,
        "remote_deleted_count": 0,
        "remote_test_deleted_count": 0,
        "skipped_remote_count": 0,
    }
    if not apply:
        return result

    remote_candidates = [item for item in planned if item["feishu_record_id"]]
    remote_delete_succeeded = set()
    active_service = service
    if remote_candidates and active_service is None:
        feishu_config = get_feishu_config()
        if feishu_config.bitable_ready and feishu_config.bitable_sync_enabled:
            active_service = BitableSyncService(config=feishu_config)
    if remote_candidates and active_service is not None:
        record_ids = [item["feishu_record_id"] for item in remote_candidates]
        remote_result = active_service.delete_records(record_ids)
        if remote_result.get("success"):
            remote_delete_succeeded.update(record_ids)
            result["remote_deleted_count"] = len(record_ids)
        else:
            result["success"] = False
            result["remote_error"] = "feishu_delete_failed"
    elif remote_candidates:
        result["success"] = False
        result["remote_error"] = "feishu_not_configured"

    deletable = [
        item
        for item in planned
        if not item["feishu_record_id"]
        or item["feishu_record_id"] in remote_delete_succeeded
    ]
    result["skipped_remote_count"] = len(planned) - len(deletable)
    if deletable:
        rowids = [item["rowid"] for item in deletable]
        uids = [item["transaction_uid"] for item in deletable if item["transaction_uid"]]
        with connect() as conn:
            if uids:
                placeholders = ",".join("?" for _ in uids)
                conn.execute(
                    f"DELETE FROM sync_outbox WHERE transaction_uid IN ({placeholders})",
                    uids,
                )
            placeholders = ",".join("?" for _ in rowids)
            cursor = conn.execute(
                f"DELETE FROM transactions WHERE rowid IN ({placeholders})",
                rowids,
            )
            result["local_deleted_count"] = int(cursor.rowcount)

    if include_remote_tests:
        if active_service is None:
            feishu_config = get_feishu_config()
            if feishu_config.bitable_ready and feishu_config.bitable_sync_enabled:
                active_service = BitableSyncService(config=feishu_config)
        if active_service is not None:
            remote_test_result = cleanup_test_records(apply=True, service=active_service)
            if remote_test_result.get("success"):
                result["remote_test_deleted_count"] = int(
                    remote_test_result.get("deleted_count") or 0
                )
            else:
                result["success"] = False
                result["remote_test_error"] = "feishu_test_cleanup_failed"
    return result


def maybe_run_scheduled_cleanup(now=None):
    """Run cleanup at most once per process-day after the configured hour."""
    global _LAST_CLEANUP_DATE
    now = now or datetime.datetime.now()
    config = get_cleanup_config()
    if not config["enabled"] or now.hour < config["hour"]:
        return {"ran": False, "reason": "disabled_or_not_due"}
    if _LAST_CLEANUP_DATE == now.date():
        return {"ran": False, "reason": "already_ran_today"}
    _LAST_CLEANUP_DATE = now.date()
    return {
        "ran": True,
        **cleanup_invalid_transactions(
            apply=True,
            now=now,
            retention_days=config["retention_days"],
        ),
    }


def _valid_date(value):
    try:
        datetime.date.fromisoformat(str(value or "")[:10])
        return True
    except (TypeError, ValueError):
        return False


def _as_datetime(value):
    try:
        parsed = datetime.datetime.fromisoformat(
            str(value or "").replace("Z", "+00:00")
        )
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError):
        return None


def _positive_finite_amount(value):
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(amount) and amount > 0


def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))

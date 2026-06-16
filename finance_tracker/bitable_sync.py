import argparse
from collections import defaultdict
import datetime
import hashlib
import json
import os
import re

try:
    import lark_oapi as lark
    from lark_oapi.api.bitable.v1 import (
        AppTableRecord,
        BatchDeleteAppTableRecordRequest,
        BatchDeleteAppTableRecordRequestBody,
        Condition,
        CreateAppTableRecordRequest,
        DeleteAppTableRequest,
        FilterInfo,
        ListAppTableFieldRequest,
        ListAppTableRequest,
        ListAppTableRecordRequest,
        SearchAppTableRecordRequest,
        SearchAppTableRecordRequestBody,
        UpdateAppTableRecordRequest,
    )
except ImportError:
    lark = None

try:
    from .feishu_client import FeishuClient, response_result
    from .feishu_config import get_feishu_config
    from .ledger import connect, init_db
except ImportError:
    from feishu_client import FeishuClient, response_result
    from feishu_config import get_feishu_config
    from ledger import connect, init_db


FIELD_MAP = {
    "transaction_uid": "交易UID",
    "id": "本地ID",
    "date": "日期",
    "type": "类型",
    "category": "分类",
    "amount": "金额",
    "description": "描述",
    "tags": "标签",
    "is_need": "是否刚需",
    "is_fixed": "是否固定",
    "source": "录入来源",
    "source_message_id": "飞书消息ID",
    "created_at": "创建时间",
    "updated_at": "更新时间",
    "status": "状态",
    "deleted_at": "删除时间",
    "deleted_by_open_id": "删除人",
    "delete_reason": "删除原因",
}
REQUIRED_FIELDS = tuple(FIELD_MAP.values())


class BitableSyncError(RuntimeError):
    def __init__(self, result):
        self.result = {
            "code": int(result.get("code", -1) or -1),
            "message": str(result.get("message") or "未知飞书 API 错误"),
            "log_id": str(result.get("log_id") or ""),
        }
        super().__init__(_format_api_error(self.result))


def auto_sync_enabled():
    config = get_feishu_config()
    return bool(
        config.bitable_sync_enabled
        and config.auto_sync
        and config.bitable_ready
    )


def transaction_to_bitable_fields(transaction):
    return {
        FIELD_MAP["transaction_uid"]: str(transaction.get("transaction_uid") or ""),
        FIELD_MAP["id"]: int(transaction.get("id") or 0),
        FIELD_MAP["date"]: _date_to_milliseconds(transaction.get("date")),
        FIELD_MAP["type"]: str(transaction.get("type") or "支出"),
        FIELD_MAP["category"]: str(transaction.get("category") or "其他"),
        FIELD_MAP["amount"]: float(transaction.get("amount") or 0),
        FIELD_MAP["description"]: str(transaction.get("description") or ""),
        FIELD_MAP["tags"]: _tag_values(transaction.get("tags")),
        FIELD_MAP["is_need"]: bool(transaction.get("is_need")),
        FIELD_MAP["is_fixed"]: bool(transaction.get("is_fixed")),
        FIELD_MAP["source"]: str(transaction.get("source") or "streamlit"),
        FIELD_MAP["source_message_id"]: str(
            transaction.get("source_message_id") or ""
        ),
        FIELD_MAP["created_at"]: _datetime_to_milliseconds(
            transaction.get("created_at")
        ),
        FIELD_MAP["updated_at"]: _datetime_to_milliseconds(
            transaction.get("updated_at")
        ),
        FIELD_MAP["status"]: str(transaction.get("status") or "active"),
        FIELD_MAP["deleted_at"]: _datetime_to_milliseconds(
            transaction.get("deleted_at")
        ),
        FIELD_MAP["deleted_by_open_id"]: str(
            _audit_identifier(transaction.get("deleted_by_open_id"))
        ),
        FIELD_MAP["delete_reason"]: str(transaction.get("delete_reason") or ""),
    }


class BitableSyncService:
    def __init__(self, client=None, config=None):
        self.config = config or get_feishu_config()
        if client is not None:
            self.client = client
        else:
            if lark is None:
                raise RuntimeError(
                    "lark-oapi is not installed. Run: pip install lark-oapi"
                )
            self.client = (
                lark.Client.builder()
                .app_id(self.config.app_id)
                .app_secret(self.config.app_secret)
                .timeout(_api_timeout_seconds())
                .build()
            )

    def create_record(self, transaction):
        record = (
            AppTableRecord.builder()
            .fields(transaction_to_bitable_fields(transaction))
            .build()
        )
        request = (
            CreateAppTableRecordRequest.builder()
            .app_token(self.config.bitable_app_token)
            .table_id(self.config.bitable_table_id)
            .request_body(record)
            .build()
        )
        result = response_result(
            self.client.bitable.v1.app_table_record.create(request)
        )
        result = _sanitize_api_result(result)
        if result["success"] and result["data"]:
            created = getattr(result["data"], "record", None)
            result["record_id"] = getattr(created, "record_id", None)
        return result

    def update_record(self, record_id, transaction):
        record = (
            AppTableRecord.builder()
            .fields(transaction_to_bitable_fields(transaction))
            .build()
        )
        request = (
            UpdateAppTableRecordRequest.builder()
            .app_token(self.config.bitable_app_token)
            .table_id(self.config.bitable_table_id)
            .record_id(record_id)
            .request_body(record)
            .build()
        )
        result = response_result(
            self.client.bitable.v1.app_table_record.update(request)
        )
        result = _sanitize_api_result(result)
        result["record_id"] = record_id
        return result

    def find_record_id(self, transaction_uid):
        result = self.search_record(transaction_uid)
        if not result["success"]:
            raise BitableSyncError(result)
        return result.get("record_id")

    def search_record(self, transaction_uid):
        result = self.search_records(transaction_uid)
        records = result.pop("records", [])
        result["record_id"] = (
            records[0]["record_id"] if len(records) == 1 else None
        )
        result["match_count"] = len(records)
        return result

    def search_records(self, transaction_uid):
        condition = (
            Condition.builder()
            .field_name(FIELD_MAP["transaction_uid"])
            .operator("is")
            .value([str(transaction_uid)])
            .build()
        )
        filter_info = (
            FilterInfo.builder()
            .conjunction("and")
            .conditions([condition])
            .build()
        )
        body = (
            SearchAppTableRecordRequestBody.builder()
            .filter(filter_info)
            .build()
        )
        records = []
        page_token = None
        while True:
            builder = (
                SearchAppTableRecordRequest.builder()
                .app_token(self.config.bitable_app_token)
                .table_id(self.config.bitable_table_id)
                .page_size(500)
                .request_body(body)
            )
            if page_token:
                builder = builder.page_token(page_token)
            result = _sanitize_api_result(
                response_result(
                    self.client.bitable.v1.app_table_record.search(
                        builder.build()
                    )
                )
            )
            if not result["success"]:
                result.pop("data", None)
                result["records"] = []
                return result
            data = result.get("data")
            records.extend(
                _remote_record(item)
                for item in (getattr(data, "items", None) or [])
            )
            if not getattr(data, "has_more", False):
                result.pop("data", None)
                result["records"] = records
                return result
            page_token = getattr(data, "page_token", None)

    def list_records(self, table_id=None):
        table_id = table_id or self.config.bitable_table_id
        records = []
        page_token = None
        while True:
            builder = (
                ListAppTableRecordRequest.builder()
                .app_token(self.config.bitable_app_token)
                .table_id(table_id)
                .page_size(500)
                .automatic_fields(True)
            )
            if page_token:
                builder = builder.page_token(page_token)
            result = _sanitize_api_result(
                response_result(
                    self.client.bitable.v1.app_table_record.list(
                        builder.build()
                    )
                )
            )
            if not result["success"]:
                result.pop("data", None)
                result["records"] = []
                return result
            data = result.get("data")
            records.extend(
                _remote_record(item)
                for item in (getattr(data, "items", None) or [])
            )
            if not getattr(data, "has_more", False):
                result.pop("data", None)
                result["records"] = records
                return result
            page_token = getattr(data, "page_token", None)

    def delete_records(self, record_ids):
        if not record_ids:
            return {
                "success": True,
                "code": 0,
                "message": "没有需要删除的远端记录。",
                "log_id": "",
                "deleted_record_ids": [],
            }
        deleted = []
        for start in range(0, len(record_ids), 500):
            batch = record_ids[start:start + 500]
            body = (
                BatchDeleteAppTableRecordRequestBody.builder()
                .records(batch)
                .build()
            )
            request = (
                BatchDeleteAppTableRecordRequest.builder()
                .app_token(self.config.bitable_app_token)
                .table_id(self.config.bitable_table_id)
                .request_body(body)
                .build()
            )
            result = _sanitize_api_result(
                response_result(
                    self.client.bitable.v1.app_table_record.batch_delete(
                        request
                    )
                )
            )
            if not result["success"]:
                result.pop("data", None)
                result["deleted_record_ids"] = deleted
                return result
            deleted.extend(
                str(getattr(item, "record_id", "") or "")
                for item in (
                    getattr(result.get("data"), "records", None) or []
                )
                if getattr(item, "deleted", False)
            )
        result.pop("data", None)
        result["deleted_record_ids"] = deleted
        return result

    def list_fields(self, table_id=None):
        table_id = table_id or self.config.bitable_table_id
        fields = []
        page_token = None
        while True:
            builder = (
                ListAppTableFieldRequest.builder()
                .app_token(self.config.bitable_app_token)
                .table_id(table_id)
                .page_size(100)
            )
            if page_token:
                builder = builder.page_token(page_token)
            result = response_result(
                self.client.bitable.v1.app_table_field.list(builder.build())
            )
            result = _sanitize_api_result(result)
            if not result["success"]:
                result.pop("data", None)
                return result
            data = result.get("data")
            items = getattr(data, "items", None) or []
            fields.extend(
                {
                    "field_name": str(
                        getattr(item, "field_name", "") or ""
                    ),
                    "field_id": str(getattr(item, "field_id", "") or ""),
                    "type": getattr(item, "type", None),
                    "ui_type": str(getattr(item, "ui_type", "") or ""),
                }
                for item in items
            )
            if not getattr(data, "has_more", False):
                result["fields"] = fields
                result.pop("data", None)
                return result
            page_token = getattr(data, "page_token", None)

    def list_tables(self):
        tables = []
        page_token = None
        while True:
            builder = (
                ListAppTableRequest.builder()
                .app_token(self.config.bitable_app_token)
                .page_size(100)
            )
            if page_token:
                builder = builder.page_token(page_token)
            result = _sanitize_api_result(
                response_result(
                    self.client.bitable.v1.app_table.list(builder.build())
                )
            )
            if not result["success"]:
                result.pop("data", None)
                result["tables"] = []
                return result
            data = result.get("data")
            tables.extend(
                {
                    "table_id": str(getattr(item, "table_id", "") or ""),
                    "name": str(getattr(item, "name", "") or ""),
                    "revision": getattr(item, "revision", None),
                }
                for item in (getattr(data, "items", None) or [])
            )
            if not getattr(data, "has_more", False):
                result.pop("data", None)
                result["tables"] = tables
                return result
            page_token = getattr(data, "page_token", None)

    def delete_table(self, table_id):
        request = (
            DeleteAppTableRequest.builder()
            .app_token(self.config.bitable_app_token)
            .table_id(str(table_id))
            .build()
        )
        result = _sanitize_api_result(
            response_result(self.client.bitable.v1.app_table.delete(request))
        )
        result.pop("data", None)
        result["table_id"] = str(table_id)
        return result


def test_bitable_connection(service=None):
    try:
        config = service.config if service is not None else get_feishu_config()
    except Exception as exc:
        return _connection_failure(
            f"读取飞书配置失败：{type(exc).__name__}"
        )
    missing = _missing_config(config)
    if missing:
        return _connection_failure(
            "飞书多维表格配置缺失：" + "、".join(missing)
        )
    try:
        service = service or BitableSyncService(config=config)
        result = service.list_fields()
        return {
            "success": bool(result.get("success")),
            "code": int(result.get("code", 0) or 0),
            "message": (
                f"连接成功，读取到 {len(result.get('fields') or [])} 个字段。"
                if result.get("success")
                else str(result.get("message") or "连接失败")
            ),
            "log_id": str(result.get("log_id") or ""),
            "field_count": len(result.get("fields") or []),
        }
    except Exception as exc:
        return _connection_failure(_safe_exception_message(exc, config))


def validate_fields(service=None):
    try:
        config = service.config if service is not None else get_feishu_config()
    except Exception as exc:
        return _field_failure(
            f"读取飞书配置失败：{type(exc).__name__}"
        )
    missing = _missing_config(config)
    if missing:
        return _field_failure(
            "飞书多维表格配置缺失：" + "、".join(missing)
        )
    try:
        service = service or BitableSyncService(config=config)
        result = service.list_fields()
        if not result.get("success"):
            return _field_failure(
                str(result.get("message") or "读取字段失败"),
                code=result.get("code", -1),
                log_id=result.get("log_id", ""),
            )
        existing_fields = sorted(
            item["field_name"]
            for item in result.get("fields") or []
            if item.get("field_name")
        )
        missing_fields = [
            name for name in REQUIRED_FIELDS if name not in existing_fields
        ]
        tag_field = next(
            (
                item
                for item in result.get("fields") or []
                if item.get("field_name") == FIELD_MAP["tags"]
            ),
            None,
        )
        invalid_field_types = []
        if tag_field and not _is_multi_select_field(tag_field):
            invalid_field_types.append(
                {
                    "field": FIELD_MAP["tags"],
                    "expected": "多选",
                    "actual_type": tag_field.get("type"),
                    "actual_ui_type": tag_field.get("ui_type", ""),
                }
            )
        field_errors = []
        if missing_fields:
            field_errors.append("目标表缺少字段：" + "、".join(missing_fields))
        if invalid_field_types:
            field_errors.append(
                f"字段“{FIELD_MAP['tags']}”必须设置为多选，否则标签数组无法同步。"
            )
        return {
            "success": not missing_fields and not invalid_field_types,
            "code": 0,
            "message": (
                "字段检查通过。"
                if not field_errors
                else "；".join(field_errors)
            ),
            "log_id": str(result.get("log_id") or ""),
            "missing_fields": missing_fields,
            "invalid_field_types": invalid_field_types,
            "existing_fields": existing_fields,
        }
    except Exception as exc:
        return _field_failure(_safe_exception_message(exc, config))


def check_bitable(service=None):
    config = service.config if service is not None else get_feishu_config()
    fields = validate_fields(service=service)
    connection = {
        "success": fields["code"] == 0,
        "code": fields["code"],
        "message": (
            f"连接成功，读取到 {len(fields['existing_fields'])} 个字段。"
            if fields["code"] == 0
            else fields["message"]
        ),
        "log_id": fields["log_id"],
        "field_count": len(fields["existing_fields"]),
    }
    return {
        "success": bool(connection["success"] and fields["success"]),
        "configuration": {
            "app_token_configured": bool(config.bitable_app_token),
            "table_id_configured": bool(config.bitable_table_id),
            "sync_enabled": bool(config.bitable_sync_enabled),
            "auto_sync": bool(config.auto_sync),
        },
        "connection": connection,
        "fields": fields,
        "message": fields["message"],
    }


def sync_transaction(
    transaction_uid,
    operation=None,
    service=None,
    trace_callback=None,
):
    init_db()
    config = service.config if service is not None else get_feishu_config()
    if not config.bitable_sync_enabled:
        result = {
            "success": False,
            "code": -1,
            "message": (
                "多维表格同步已关闭："
                "FEISHU_BITABLE_SYNC_ENABLED=false"
            ),
            "log_id": "",
        }
        _finish_sync(
            transaction_uid,
            None,
            success=False,
            error=result["message"],
        )
        return result
    if not config.bitable_ready:
        result = {
            "success": False,
            "code": -1,
            "message": (
                "飞书多维表格配置不完整："
                + "、".join(_missing_config(config))
            ),
            "log_id": "",
        }
        _finish_sync(
            transaction_uid,
            None,
            success=False,
            error=result["message"],
        )
        return result
    service = service or BitableSyncService(config=config)

    with connect() as conn:
        row = conn.execute(
            """
            SELECT rowid, id, date, type, category, amount, description,
                   created_at, tags, is_need, is_fixed, transaction_uid,
                   source, source_message_id, feishu_record_id, updated_at,
                   sync_status, sync_error, source_user_open_id,
                   source_chat_id, deleted_at, deleted_by_open_id,
                   delete_reason, status
            FROM transactions
            WHERE transaction_uid = ?
            """,
            (transaction_uid,),
        ).fetchone()
        columns = [
            "_rowid", "id", "date", "type", "category", "amount",
            "description", "created_at", "tags", "is_need", "is_fixed",
            "transaction_uid", "source", "source_message_id",
            "feishu_record_id", "updated_at", "sync_status", "sync_error",
            "source_user_open_id", "source_chat_id", "deleted_at",
            "deleted_by_open_id", "delete_reason", "status",
        ]
        transaction = dict(zip(columns, row)) if row else None
        outbox = conn.execute(
            """
            SELECT id, operation FROM sync_outbox
            WHERE transaction_uid = ? AND status IN ('pending', 'failed')
            ORDER BY id ASC LIMIT 1
            """,
            (transaction_uid,),
        ).fetchone()
        operation = operation or (outbox[1] if outbox else "update")
        outbox_id = outbox[0] if outbox else None

    try:
        if not transaction:
            result = {
                "success": False,
                "code": -1,
                "message": "本地流水不存在。",
                "log_id": "",
            }
        else:
            local_record_id = transaction.get("feishu_record_id")
            search_result = _search_record_result(
                service,
                transaction_uid,
            )
            _emit_trace(
                trace_callback,
                "search",
                {
                    **search_result,
                    "record_id_found": bool(
                        search_result.get("record_id")
                        or local_record_id
                    ),
                },
            )
            if not search_result.get("success"):
                raise BitableSyncError(search_result)
            if int(search_result.get("match_count", 0) or 0) > 1:
                raise BitableSyncError(
                    {
                        "code": -2,
                        "message": (
                            "远端存在多个相同交易UID，已停止同步；"
                            "请先运行 --dedupe-remote --dry-run。"
                        ),
                        "log_id": search_result.get("log_id", ""),
                    }
                )
            record_id = (
                search_result.get("record_id") or local_record_id
            )
            result = (
                service.update_record(record_id, transaction)
                if record_id
                else service.create_record(transaction)
            )
            _emit_trace(
                trace_callback,
                "update" if record_id else "create",
                result,
            )
            if result.get("record_id"):
                record_id = result["record_id"]

        error = "" if result.get("success") else _format_api_error(result)
        _finish_sync(
            transaction_uid,
            outbox_id,
            success=bool(result.get("success")),
            error=error,
            record_id=locals().get("record_id"),
        )
        result.pop("data", None)
        return result
    except BitableSyncError as exc:
        error = _format_api_error(exc.result)
        _finish_sync(
            transaction_uid,
            outbox_id,
            success=False,
            error=error,
        )
        return {"success": False, **exc.result}
    except Exception as exc:
        error = _safe_exception_message(exc, config)
        _finish_sync(
            transaction_uid,
            outbox_id,
            success=False,
            error=error,
        )
        return {
            "success": False,
            "code": -1,
            "message": error,
            "log_id": "",
        }


def sync_pending_transactions(limit=100, service=None, progress_callback=None):
    init_db()
    config = service.config if service is not None else get_feishu_config()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT outbox.transaction_uid, outbox.operation
            FROM sync_outbox AS outbox
            INNER JOIN (
                SELECT transaction_uid, MAX(id) AS latest_id
                FROM sync_outbox
                WHERE status IN ('pending', 'failed') AND retry_count < ?
                GROUP BY transaction_uid
            ) AS latest ON latest.latest_id = outbox.id
            ORDER BY outbox.id ASC LIMIT ?
            """,
            (config.sync_retry_limit, int(limit)),
        ).fetchall()
    if not rows:
        return _sync_summary("没有待同步任务", [])
    disabled = _sync_disabled_result(config)
    if disabled:
        error = _format_api_error(disabled)
        _mark_sync_batch_failed([row[0] for row in rows], error)
        return _sync_summary(
            "同步前检查失败",
            [],
            preflight=disabled,
            processed=len(rows),
        )
    preflight = validate_fields(service=service)
    if not preflight["success"]:
        error = _format_api_error(preflight)
        _mark_sync_batch_failed([row[0] for row in rows], error)
        return _sync_summary(
            "同步前检查失败",
            [],
            preflight=preflight,
            processed=len(rows),
        )
    service = service or BitableSyncService(config=config)
    results = _sync_rows(
        rows,
        service,
        progress_callback=progress_callback,
    )
    return _sync_summary("同步完成", results)


def full_sync(service=None, progress_callback=None):
    init_db()
    config = service.config if service is not None else get_feishu_config()
    with connect() as conn:
        transaction_uids = [
            row[0]
            for row in conn.execute(
                "SELECT transaction_uid FROM transactions ORDER BY rowid ASC"
            ).fetchall()
        ]
    if not transaction_uids:
        return _sync_summary("本地账本没有可同步流水", [])
    disabled = _sync_disabled_result(config)
    if disabled:
        error = _format_api_error(disabled)
        _mark_sync_batch_failed(
            transaction_uids,
            error,
            update_outbox=False,
        )
        return _sync_summary(
            "全量同步前检查失败",
            [],
            preflight=disabled,
            processed=len(transaction_uids),
        )
    preflight = validate_fields(service=service)
    if not preflight["success"]:
        error = _format_api_error(preflight)
        _mark_sync_batch_failed(
            transaction_uids,
            error,
            update_outbox=False,
        )
        return _sync_summary(
            "全量同步前检查失败",
            [],
            preflight=preflight,
            processed=len(transaction_uids),
        )
    service = service or BitableSyncService(config=config)
    rows = [(uid, "update") for uid in transaction_uids]
    results = _sync_rows(
        rows,
        service,
        progress_callback=progress_callback,
    )
    return _sync_summary("全量同步完成", results)


def sync_one_pending(service=None):
    init_db()
    config = service.config if service is not None else get_feishu_config()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT transactions.id, transactions.transaction_uid,
                   outbox.operation
            FROM sync_outbox AS outbox
            INNER JOIN transactions
                ON transactions.transaction_uid = outbox.transaction_uid
            WHERE outbox.status IN ('pending', 'failed')
            ORDER BY outbox.id ASC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return {
            "local_id": None,
            "transaction_uid_prefix": "",
            "success": True,
            "code": 0,
            "message": "没有待同步或失败流水。",
            "log_id": "",
        }
    local_id, transaction_uid, operation = row
    base = {
        "local_id": int(local_id),
        "transaction_uid_prefix": str(transaction_uid)[:8],
    }
    disabled = _sync_disabled_result(config)
    if disabled:
        return {**base, **_one_result(disabled)}
    service = service or BitableSyncService(config=config)
    preflight = validate_fields(service=service)
    if not preflight["success"]:
        error = _format_api_error(preflight)
        _mark_sync_batch_failed([transaction_uid], error)
        return {**base, **_one_result(preflight)}
    steps = []
    result = sync_transaction(
        transaction_uid,
        operation=operation,
        service=service,
        trace_callback=steps.append,
    )
    return {**base, "steps": steps, **_one_result(result)}


def reset_failed_sync():
    """Move failed local sync state back to pending without deleting data."""
    init_db()
    with connect() as conn:
        transaction_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM transactions
            WHERE sync_status = 'failed'
            """
        ).fetchone()[0]
        outbox_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM sync_outbox
            WHERE status = 'failed'
            """
        ).fetchone()[0]
        conn.execute(
            """
            UPDATE transactions
            SET sync_status = 'pending', sync_error = ''
            WHERE sync_status = 'failed'
            """
        )
        conn.execute(
            """
            UPDATE sync_outbox
            SET status = 'pending',
                retry_count = 0,
                last_error = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'failed'
            """
        )
    return {
        "success": True,
        "transactions_reset": int(transaction_count),
        "outbox_reset": int(outbox_count),
        "message": (
            f"已重置 {transaction_count} 条失败流水和 "
            f"{outbox_count} 条失败 outbox 任务。"
        ),
    }


def get_sync_dashboard(service=None, check_fields=True):
    """Return safe sync-management data for Streamlit and Feishu cards."""
    init_db()
    config = service.config if service is not None else get_feishu_config()
    with connect() as conn:
        counts = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN sync_status = 'synced' THEN 1 ELSE 0 END),
                SUM(CASE WHEN sync_status = 'failed' THEN 1 ELSE 0 END),
                SUM(
                    CASE
                        WHEN sync_status IS NULL
                          OR sync_status = ''
                          OR sync_status = 'pending'
                        THEN 1 ELSE 0
                    END
                ),
                SUM(
                    CASE
                        WHEN feishu_record_id IS NOT NULL
                         AND TRIM(feishu_record_id) <> ''
                        THEN 1 ELSE 0
                    END
                )
            FROM transactions
            """
        ).fetchone()
        recent_rows = conn.execute(
            """
            SELECT id, transaction_uid, sync_error, updated_at
            FROM transactions
            WHERE sync_status = 'failed'
              AND sync_error IS NOT NULL
              AND TRIM(sync_error) <> ''
            ORDER BY COALESCE(updated_at, created_at) DESC, rowid DESC
            LIMIT 5
            """
        ).fetchall()

    field_status = (
        validate_fields(service=service)
        if check_fields
        else {
            "success": None,
            "code": 0,
            "message": "尚未执行字段检查。",
            "log_id": "",
            "missing_fields": [],
            "existing_fields": [],
        }
    )
    return {
        "success": bool(
            config.bitable_ready
            and config.bitable_sync_enabled
            and field_status.get("success") is not False
        ),
        "configuration": {
            "bot_ready": bool(config.bot_ready),
            "bitable_ready": bool(config.bitable_ready),
            "app_token_configured": bool(config.bitable_app_token),
            "table_id_configured": bool(config.bitable_table_id),
            "sync_enabled": bool(config.bitable_sync_enabled),
            "auto_sync": bool(config.auto_sync),
        },
        "fields": field_status,
        "counts": {
            "total": int(counts[0] or 0),
            "synced": int(counts[1] or 0),
            "failed": int(counts[2] or 0),
            "pending": int(counts[3] or 0),
            "record_id": int(counts[4] or 0),
        },
        "recent_errors": [
            {
                "local_id": int(row[0]),
                "transaction_uid_prefix": str(row[1] or "")[:8],
                "message": _sanitize_api_message(str(row[2] or "")),
                "updated_at": str(row[3] or ""),
            }
            for row in recent_rows
        ],
    }


def audit_remote(service=None):
    init_db()
    config = service.config if service is not None else get_feishu_config()
    disabled = _sync_disabled_result(config)
    if disabled:
        return disabled
    service = service or BitableSyncService(config=config)
    remote_result = service.list_records()
    if not remote_result.get("success"):
        return _one_result(remote_result)

    remote_records = remote_result.get("records") or []
    with connect() as conn:
        local_rows = conn.execute(
            """
            SELECT transaction_uid, feishu_record_id
            FROM transactions
            """
        ).fetchall()
    local_uids = {
        str(row[0]).strip()
        for row in local_rows
        if str(row[0] or "").strip()
    }
    remote_uid_records = defaultdict(list)
    empty_uid_records = []
    test_uid_records = []
    permission_test_records = []
    for record in remote_records:
        uid = _record_uid(record)
        if uid:
            remote_uid_records[uid].append(record)
        else:
            empty_uid_records.append(record)
        if uid.lower().startswith("test_"):
            test_uid_records.append(record)
        if "权限测试" in _field_text(
            record["fields"].get(FIELD_MAP["description"])
        ):
            permission_test_records.append(record)

    duplicate_groups = {
        uid: records
        for uid, records in remote_uid_records.items()
        if len(records) > 1
    }
    remote_uids = set(remote_uid_records)
    orphan_uids = sorted(remote_uids - local_uids)
    missing_remote_uids = sorted(local_uids - remote_uids)
    obvious_test_ids = {
        record["record_id"]
        for record in _test_records(remote_records)
    }
    return {
        "success": True,
        "code": 0,
        "message": "远端审计完成。",
        "log_id": str(remote_result.get("log_id") or ""),
        "local_total": len(local_rows),
        "local_uid_unique": len(local_uids),
        "remote_total": len(remote_records),
        "remote_uid_empty": len(empty_uid_records),
        "remote_uid_unique": len(remote_uids),
        "remote_duplicate_uid_count": len(duplicate_groups),
        "remote_duplicate_extra_records": sum(
            len(records) - 1
            for records in duplicate_groups.values()
        ),
        "remote_orphan_uid_count": len(orphan_uids),
        "local_missing_remote_uid_count": len(missing_remote_uids),
        "remote_test_uid_count": len(test_uid_records),
        "remote_permission_test_count": len(permission_test_records),
        "remote_obvious_test_record_count": len(obvious_test_ids),
        "duplicate_uid_examples": [
            {
                "transaction_uid_prefix": uid[:8],
                "record_count": len(records),
                "record_ids": [
                    record["record_id"] for record in records[:5]
                ],
            }
            for uid, records in list(sorted(duplicate_groups.items()))[:10]
        ],
        "orphan_record_examples": [
            {
                "transaction_uid_prefix": uid[:8],
                "record_count": len(remote_uid_records[uid]),
                "record_ids": [
                    record["record_id"]
                    for record in remote_uid_records[uid][:3]
                ],
            }
            for uid in orphan_uids[:10]
        ],
        "empty_uid_examples": [
            {"record_id": record["record_id"]}
            for record in empty_uid_records[:10]
        ],
        "test_uid_examples": [
            {
                "transaction_uid_prefix": _record_uid(record)[:8],
                "record_id": record["record_id"],
            }
            for record in test_uid_records[:10]
        ],
        "permission_test_examples": [
            {"record_id": record["record_id"]}
            for record in permission_test_records[:10]
        ],
        "local_missing_remote_uid_examples": [
            uid[:8] for uid in missing_remote_uids[:10]
        ],
    }


def dedupe_remote(apply=False, service=None):
    init_db()
    config = service.config if service is not None else get_feishu_config()
    service = service or BitableSyncService(config=config)
    remote_result = service.list_records()
    if not remote_result.get("success"):
        return _one_result(remote_result)
    remote_records = remote_result.get("records") or []
    groups = defaultdict(list)
    for record in remote_records:
        uid = _record_uid(record)
        if uid:
            groups[uid].append(record)

    with connect() as conn:
        local_bindings = {
            str(row[0]).strip(): str(row[1] or "").strip()
            for row in conn.execute(
                """
                SELECT transaction_uid, feishu_record_id
                FROM transactions
                """
            ).fetchall()
            if str(row[0] or "").strip()
        }

    keepers = {}
    planned_deletions = []
    duplicate_groups = 0
    for uid, records in groups.items():
        if len(records) < 2:
            continue
        duplicate_groups += 1
        keeper = _choose_keeper(records, local_bindings.get(uid))
        keepers[uid] = keeper["record_id"]
        planned_deletions.extend(
            {
                "record_id": record["record_id"],
                "transaction_uid_prefix": uid[:8],
                "kept_record_id": keeper["record_id"],
            }
            for record in records
            if record["record_id"] != keeper["record_id"]
        )

    result = {
        "success": True,
        "code": 0,
        "message": (
            "远端去重 dry-run 完成。"
            if not apply
            else "远端去重完成。"
        ),
        "log_id": str(remote_result.get("log_id") or ""),
        "mode": "apply" if apply else "dry-run",
        "duplicate_uid_count": duplicate_groups,
        "planned_delete_count": len(planned_deletions),
        "planned_deletions": planned_deletions,
        "deleted_count": 0,
        "local_bindings_updated": 0,
    }
    if not apply or not planned_deletions:
        return result

    delete_result = service.delete_records(
        [item["record_id"] for item in planned_deletions]
    )
    if not delete_result.get("success"):
        return {
            **result,
            **_one_result(delete_result),
            "message": "远端重复记录删除失败。",
            "deleted_count": len(
                delete_result.get("deleted_record_ids") or []
            ),
        }
    with connect() as conn:
        updated = 0
        for uid, record_id in keepers.items():
            cursor = conn.execute(
                """
                UPDATE transactions
                SET feishu_record_id = ?
                WHERE transaction_uid = ?
                  AND COALESCE(feishu_record_id, '') <> ?
                """,
                (record_id, uid, record_id),
            )
            updated += cursor.rowcount
    result["deleted_count"] = len(
        delete_result.get("deleted_record_ids") or []
    )
    result["local_bindings_updated"] = updated
    return result


def cleanup_test_records(apply=False, service=None):
    init_db()
    config = service.config if service is not None else get_feishu_config()
    service = service or BitableSyncService(config=config)
    remote_result = service.list_records()
    if not remote_result.get("success"):
        return _one_result(remote_result)
    planned_records = _test_records(remote_result.get("records") or [])
    planned = [
        {
            "record_id": record["record_id"],
            "transaction_uid_prefix": _record_uid(record)[:8],
            "reason": _test_record_reason(record),
        }
        for record in planned_records
    ]
    result = {
        "success": True,
        "code": 0,
        "message": (
            "测试记录清理 dry-run 完成。"
            if not apply
            else "测试记录清理完成。"
        ),
        "log_id": str(remote_result.get("log_id") or ""),
        "mode": "apply" if apply else "dry-run",
        "planned_delete_count": len(planned),
        "planned_deletions": planned,
        "deleted_count": 0,
    }
    if not apply or not planned:
        return result
    delete_result = service.delete_records(
        [item["record_id"] for item in planned]
    )
    if not delete_result.get("success"):
        return {
            **result,
            **_one_result(delete_result),
            "message": "远端测试记录删除失败。",
            "deleted_count": len(
                delete_result.get("deleted_record_ids") or []
            ),
        }
    result["deleted_count"] = len(
        delete_result.get("deleted_record_ids") or []
    )
    return result


def list_tables(service=None):
    try:
        config = service.config if service is not None else get_feishu_config()
    except Exception as exc:
        return _table_failure(f"读取飞书配置失败：{type(exc).__name__}")
    missing = _missing_config(config)
    if missing:
        return _table_failure(
            "飞书多维表格配置缺失：" + "、".join(missing)
        )
    try:
        service = service or BitableSyncService(config=config)
        tables_result = service.list_tables()
    except Exception as exc:
        return _table_failure(
            "读取飞书数据表失败："
            + _safe_exception_message(exc, config)
        )
    if not tables_result.get("success"):
        return {
            **_table_failure("读取飞书数据表失败。"),
            **_one_result(tables_result),
        }

    original_table_id = str(config.bitable_table_id or "").strip()
    tables = []
    first_error = None
    for table in tables_result.get("tables") or []:
        table_id = str(table.get("table_id") or "").strip()
        item = {
            "name": str(table.get("name") or ""),
            "table_id": table_id,
            "is_original": table_id == original_table_id,
            "field_count": None,
            "record_count": None,
            "field_error": None,
            "record_error": None,
        }
        try:
            fields_result = service.list_fields(table_id=table_id)
        except Exception as exc:
            fields_result = {
                "success": False,
                "code": -1,
                "message": _safe_exception_message(exc, config),
                "log_id": "",
            }
        if fields_result.get("success"):
            item["field_count"] = len(fields_result.get("fields") or [])
        else:
            item["field_error"] = _api_error_dict(fields_result)
            first_error = first_error or fields_result
        try:
            records_result = service.list_records(table_id=table_id)
        except Exception as exc:
            records_result = {
                "success": False,
                "code": -1,
                "message": _safe_exception_message(exc, config),
                "log_id": "",
            }
        if records_result.get("success"):
            item["record_count"] = len(records_result.get("records") or [])
        else:
            item["record_error"] = _api_error_dict(records_result)
            first_error = first_error or records_result
        tables.append(item)

    success = first_error is None
    return {
        "success": success,
        "code": int(
            (first_error.get("code", 0) if first_error else 0) or 0
        ),
        "message": (
            "数据表列表读取完成。"
            if success
            else "数据表列表读取完成，但部分表的字段或记录统计失败。"
        ),
        "log_id": str(
            first_error.get("log_id", "")
            if first_error
            else tables_result.get("log_id", "")
        ),
        "original_table_id": original_table_id,
        "table_count": len(tables),
        "tables": tables,
    }


def cleanup_summary_tables(
    apply=False,
    confirm_delete_summary_tables=False,
    service=None,
):
    try:
        config = service.config if service is not None else get_feishu_config()
    except Exception as exc:
        return _table_failure(f"读取飞书配置失败：{type(exc).__name__}")
    missing = _missing_config(config)
    if missing:
        return _table_failure(
            "飞书多维表格配置缺失：" + "、".join(missing)
        )
    if apply and not confirm_delete_summary_tables:
        return {
            **_table_failure(
                "删除飞书非原始表需要额外确认参数："
                "--confirm-delete-summary-tables"
            ),
            "mode": "apply",
            "original_table_id": str(config.bitable_table_id or "").strip(),
            "planned_delete_count": 0,
            "planned_deletions": [],
            "deleted_count": 0,
            "deleted_tables": [],
        }
    try:
        service = service or BitableSyncService(config=config)
        tables_result = service.list_tables()
    except Exception as exc:
        return _table_failure(
            "读取飞书数据表失败："
            + _safe_exception_message(exc, config)
        )
    if not tables_result.get("success"):
        return {
            **_table_failure("读取飞书数据表失败。"),
            **_one_result(tables_result),
        }

    original_table_id = str(config.bitable_table_id or "").strip()
    planned = [
        {
            "name": str(table.get("name") or ""),
            "table_id": str(table.get("table_id") or "").strip(),
        }
        for table in tables_result.get("tables") or []
        if str(table.get("table_id") or "").strip()
        and str(table.get("table_id") or "").strip() != original_table_id
    ]
    result = {
        "success": True,
        "code": 0,
        "message": (
            "飞书非原始表清理 dry-run 完成。"
            if not apply
            else "飞书非原始表清理完成。"
        ),
        "log_id": str(tables_result.get("log_id") or ""),
        "mode": "apply" if apply else "dry-run",
        "original_table_id": original_table_id,
        "planned_delete_count": len(planned),
        "planned_deletions": planned,
        "deleted_count": 0,
        "deleted_tables": [],
        "original_table_protected": True,
    }
    if not apply or not planned:
        return result

    for item in planned:
        table_id = item["table_id"]
        if table_id == original_table_id:
            continue
        try:
            delete_result = service.delete_table(table_id)
        except Exception as exc:
            delete_result = {
                "success": False,
                "code": -1,
                "message": _safe_exception_message(exc, config),
                "log_id": "",
            }
        if not delete_result.get("success"):
            return {
                **result,
                **_one_result(delete_result),
                "message": (
                    "飞书非原始表删除失败。原始数据表未删除。"
                ),
            }
        result["deleted_tables"].append(item)
    result["deleted_count"] = len(result["deleted_tables"])
    return result


def _sync_rows(rows, service, progress_callback=None):
    total = len(rows)
    results = []
    for index, (transaction_uid, operation) in enumerate(rows, start=1):
        result = sync_transaction(
            transaction_uid,
            operation=operation,
            service=service,
        )
        results.append(result)
        if progress_callback and not result.get("success"):
            local_id, uid_prefix = _safe_transaction_identity(
                transaction_uid
            )
            progress_callback(
                {
                    "event": "error",
                    "processed": index,
                    "total": total,
                    "local_id": local_id,
                    "transaction_uid_prefix": uid_prefix,
                    "success": False,
                    "code": int(result.get("code", -1) or -1),
                    "message": _sanitize_api_message(
                        str(result.get("message") or "未知错误")
                    ),
                    "log_id": str(result.get("log_id") or ""),
                }
            )
        if progress_callback and (index % 10 == 0 or index == total):
            progress_callback(
                {
                    "event": "progress",
                    "processed": index,
                    "total": total,
                    "succeeded": sum(
                        bool(item.get("success")) for item in results
                    ),
                    "failed": sum(
                        not item.get("success") for item in results
                    ),
                }
            )
    return results


def _safe_transaction_identity(transaction_uid):
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM transactions
            WHERE transaction_uid = ?
            """,
            (transaction_uid,),
        ).fetchone()
    return (
        int(row[0]) if row and row[0] is not None else None,
        str(transaction_uid or "")[:8],
    )


def _sync_summary(label, results, preflight=None, processed=None):
    succeeded = sum(bool(item.get("success")) for item in results)
    failed = (
        max(0, int(processed) - succeeded)
        if processed is not None
        else sum(not item.get("success") for item in results)
    )
    processed = int(processed) if processed is not None else len(results)
    errors = []
    diagnostic = None
    if preflight and not preflight.get("success"):
        diagnostic = preflight
        errors.append(_format_api_error(preflight))
    for item in results:
        if not item.get("success"):
            if diagnostic is None:
                diagnostic = item
            error = _format_api_error(item)
            if error not in errors:
                errors.append(error)
        if len(errors) >= 3:
            break
    message = f"{label}：成功 {succeeded}，失败 {failed}。"
    if errors:
        message += " 原因：" + "；".join(errors)
    return {
        "success": failed == 0,
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
        "errors": errors,
        "code": int(
            diagnostic.get("code", -1) if diagnostic else 0
        ),
        "log_id": str(
            diagnostic.get("log_id") or "" if diagnostic else ""
        ),
        "message": message,
    }


def _mark_sync_batch_failed(transaction_uids, error, update_outbox=True):
    if not transaction_uids:
        return
    with connect() as conn:
        for transaction_uid in transaction_uids:
            conn.execute(
                """
                UPDATE transactions
                SET sync_status = 'failed', sync_error = ?
                WHERE transaction_uid = ?
                """,
                (error[:1000], transaction_uid),
            )
            if update_outbox:
                conn.execute(
                    """
                    UPDATE sync_outbox
                    SET status = 'failed',
                        retry_count = retry_count + 1,
                        last_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = (
                        SELECT id FROM sync_outbox
                        WHERE transaction_uid = ?
                          AND status IN ('pending', 'failed')
                        ORDER BY id DESC LIMIT 1
                    )
                    """,
                    (error[:1000], transaction_uid),
                )


def _finish_sync(
    transaction_uid,
    outbox_id,
    success,
    error="",
    record_id=None,
):
    status = "synced" if success else "failed"
    with connect() as conn:
        if success:
            conn.execute(
                """
                UPDATE sync_outbox
                SET status = 'done', last_error = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE transaction_uid = ?
                  AND status IN ('pending', 'failed')
                """,
                (transaction_uid,),
            )
        elif outbox_id:
            conn.execute(
                """
                UPDATE sync_outbox
                SET status = 'failed',
                    retry_count = retry_count + 1,
                    last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (error[:1000], outbox_id),
            )
        conn.execute(
            """
            UPDATE transactions
            SET sync_status = ?, sync_error = ?,
                feishu_record_id = COALESCE(?, feishu_record_id),
                updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP)
            WHERE transaction_uid = ?
            """,
            (
                status,
                "" if success else error[:1000],
                record_id,
                transaction_uid,
            ),
        )


def _missing_config(config):
    missing = []
    if not config.app_id:
        missing.append("FEISHU_APP_ID")
    if not config.app_secret:
        missing.append("FEISHU_APP_SECRET")
    if not config.bitable_app_token:
        missing.append("FEISHU_BITABLE_APP_TOKEN")
    if not config.bitable_table_id:
        missing.append("FEISHU_BITABLE_TABLE_ID")
    return missing


def _sync_disabled_result(config):
    if config.bitable_sync_enabled:
        return None
    return {
        "success": False,
        "code": -1,
        "message": (
            "多维表格同步已关闭："
            "FEISHU_BITABLE_SYNC_ENABLED=false"
        ),
        "log_id": "",
    }


def _one_result(result):
    return {
        "success": bool(result.get("success")),
        "code": int(result.get("code", 0) or 0),
        "message": _sanitize_api_message(
            str(result.get("message") or "")
        ),
        "log_id": str(result.get("log_id") or ""),
    }


def _emit_trace(callback, step, result):
    if callback is None:
        return
    callback(
        {
            "step": step,
            "success": bool(result.get("success")),
            "code": int(result.get("code", 0) or 0),
            "message": _sanitize_api_message(
                str(result.get("message") or "")
            ),
            "log_id": str(result.get("log_id") or ""),
            **(
                {
                    "record_id_found": bool(
                        result.get("record_id_found")
                    )
                }
                if step == "search"
                else {}
            ),
        }
    )


def _search_record_result(service, transaction_uid):
    if hasattr(service, "search_record"):
        return service.search_record(transaction_uid)
    record_id = service.find_record_id(transaction_uid)
    return {
        "success": True,
        "code": 0,
        "message": "",
        "log_id": "",
        "record_id": record_id,
        "match_count": 1 if record_id else 0,
    }


def _remote_record(item):
    return {
        "record_id": str(getattr(item, "record_id", "") or ""),
        "created_time": int(getattr(item, "created_time", 0) or 0),
        "last_modified_time": int(
            getattr(item, "last_modified_time", 0) or 0
        ),
        "fields": dict(getattr(item, "fields", None) or {}),
    }


def _field_text(value):
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("text", "value", "name"):
            if key in value:
                return _field_text(value[key])
        return ""
    if isinstance(value, (list, tuple)):
        return "".join(_field_text(item) for item in value)
    return str(value).strip()


def _record_uid(record):
    return _field_text(
        record.get("fields", {}).get(FIELD_MAP["transaction_uid"])
    )


def _field_is_present(value):
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return bool(str(value).strip())


def _choose_keeper(records, local_record_id=""):
    if local_record_id:
        for record in records:
            if record["record_id"] == local_record_id:
                return record
    return max(
        records,
        key=lambda record: (
            sum(
                _field_is_present(value)
                for value in record.get("fields", {}).values()
            ),
            int(record.get("created_time") or 0),
            int(record.get("last_modified_time") or 0),
        ),
    )


def _local_id_is_empty(record):
    value = _field_text(
        record.get("fields", {}).get(FIELD_MAP["id"])
    )
    if not value:
        return True
    try:
        return float(value) == 0
    except (TypeError, ValueError):
        return False


def _test_record_reason(record):
    uid = _record_uid(record)
    description = _field_text(
        record.get("fields", {}).get(FIELD_MAP["description"])
    )
    reasons = []
    if not uid and _local_id_is_empty(record):
        reasons.append("交易UID为空且本地ID为空或0")
    if uid.lower().startswith("test_"):
        reasons.append("交易UID以test_开头")
    if "权限测试" in description:
        reasons.append("描述包含权限测试")
    return "；".join(reasons)


def _test_records(records):
    return [
        record for record in records
        if _test_record_reason(record)
    ]


def _connection_failure(message, code=-1, log_id=""):
    return {
        "success": False,
        "code": int(code or -1),
        "message": str(message),
        "log_id": str(log_id or ""),
        "field_count": 0,
    }


def _table_failure(message, code=-1, log_id=""):
    return {
        "success": False,
        "code": int(code or -1),
        "message": _sanitize_api_message(str(message)),
        "log_id": str(log_id or ""),
        "original_table_id": "",
        "table_count": 0,
        "tables": [],
    }


def _field_failure(message, code=-1, log_id=""):
    return {
        "success": False,
        "code": int(code or -1),
        "message": str(message),
        "log_id": str(log_id or ""),
        "missing_fields": [],
        "existing_fields": [],
    }


def _api_error_dict(result):
    return {
        "code": int(result.get("code", -1) or -1),
        "message": _sanitize_api_message(
            str(result.get("message") or "未知错误")
        ),
        "log_id": str(result.get("log_id") or ""),
    }


def _format_api_error(result):
    code = int(result.get("code", -1) or -1)
    message = _sanitize_api_message(
        str(result.get("message") or "未知错误").strip()
    )
    log_id = str(result.get("log_id") or "").strip()
    details = f"code={code}, message={message}"
    if log_id:
        details += f", log_id={log_id}"
    return details


def _safe_exception_message(exc, config):
    message = str(exc) or type(exc).__name__
    for secret in (
        config.app_secret,
        config.bitable_app_token,
        config.bitable_table_id,
    ):
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return f"{type(exc).__name__}: {message}"


def _sanitize_api_result(result):
    safe = dict(result)
    safe["message"] = _sanitize_api_message(safe.get("message", ""))
    return safe


def _sanitize_api_message(message):
    text = str(message or "")
    text = re.sub(
        r"https?://\S+",
        "[飞书权限申请链接已省略]",
        text,
    )
    text = re.sub(r"\bcli_[A-Za-z0-9]+\b", "[APP_ID]", text)
    text = re.sub(r"\b(?:ou|oc)_[A-Za-z0-9]+\b", "[ID]", text)
    text = re.sub(
        r"(?i)\b(?:tenant_)?access_token\b\s*[:=]\s*\S+",
        "access_token=[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+",
        "Bearer [REDACTED]",
        text,
    )
    return text


def _audit_identifier(value):
    text = str(value or "").strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _api_timeout_seconds():
    try:
        value = float(
            os.getenv("FEISHU_BITABLE_TIMEOUT_SECONDS", "15")
        )
    except (TypeError, ValueError):
        value = 15
    return max(3.0, min(value, 60.0))


def _print_cli_event(event):
    if event.get("event") == "error":
        message = (
            f"[失败 {event['processed']}/{event['total']}] "
            f"本地ID={event.get('local_id')} "
            f"UID={event.get('transaction_uid_prefix', '')} "
            f"code={event['code']} message={event['message']}"
        )
        if event.get("log_id"):
            message += f" log_id={event['log_id']}"
        print(message, flush=True)
        return
    print(
        f"[进度 {event['processed']}/{event['total']}] "
        f"成功 {event['succeeded']}，失败 {event['failed']}",
        flush=True,
    )


def _date_to_milliseconds(value):
    parsed = datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d")
    return int(parsed.timestamp() * 1000)


def _datetime_to_milliseconds(value):
    if not value:
        return None
    text = str(value).replace("T", " ")
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


def _tag_values(value):
    if isinstance(value, list):
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]
    return [
        item.strip()
        for item in str(value or "").replace("，", ",").split(",")
        if item.strip()
    ]


def _is_multi_select_field(field):
    field_type = field.get("type")
    ui_type = str(field.get("ui_type") or "").strip().lower()
    if field_type in (None, "") and not ui_type:
        return True
    return str(field_type) == "4" or ui_type in {
        "multiselect",
        "multi_select",
        "multiple_select",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Sync finance transactions to Feishu Bitable."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--one", action="store_true")
    group.add_argument("--pending", action="store_true")
    group.add_argument("--full", action="store_true")
    group.add_argument("--reset-failed", action="store_true")
    group.add_argument("--audit-remote", action="store_true")
    group.add_argument("--dedupe-remote", action="store_true")
    group.add_argument("--cleanup-test-records", action="store_true")
    group.add_argument("--list-tables", action="store_true")
    group.add_argument("--cleanup-summary-tables", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--confirm-delete-summary-tables",
        action="store_true",
        help="Required with --cleanup-summary-tables --apply.",
    )
    args = parser.parse_args()
    destructive_command = (
        args.dedupe_remote
        or args.cleanup_test_records
        or args.cleanup_summary_tables
    )
    if destructive_command and not (args.dry_run or args.apply):
        parser.error(
            "--dedupe-remote, --cleanup-test-records and "
            "--cleanup-summary-tables require --dry-run or --apply"
        )
    if not destructive_command and (args.dry_run or args.apply):
        parser.error(
            "--dry-run/--apply can only be used with "
            "--dedupe-remote, --cleanup-test-records or "
            "--cleanup-summary-tables"
        )
    if (
        args.confirm_delete_summary_tables
        and not args.cleanup_summary_tables
    ):
        parser.error(
            "--confirm-delete-summary-tables can only be used with "
            "--cleanup-summary-tables"
        )
    if args.check:
        result = check_bitable()
    elif args.one:
        result = sync_one_pending()
    elif args.pending:
        print(
            f"[开始] 同步待处理流水，单次 API 超时 "
            f"{_api_timeout_seconds():g} 秒。",
            flush=True,
        )
        result = sync_pending_transactions(
            progress_callback=_print_cli_event
        )
    elif args.full:
        print(
            f"[开始] 全量同步，单次 API 超时 "
            f"{_api_timeout_seconds():g} 秒。",
            flush=True,
        )
        result = full_sync(progress_callback=_print_cli_event)
    elif args.reset_failed:
        result = reset_failed_sync()
    elif args.audit_remote:
        result = audit_remote()
    elif args.dedupe_remote:
        result = dedupe_remote(apply=args.apply)
    elif args.cleanup_test_records:
        result = cleanup_test_records(apply=args.apply)
    elif args.list_tables:
        result = list_tables()
    else:
        result = cleanup_summary_tables(
            apply=args.apply,
            confirm_delete_summary_tables=(
                args.confirm_delete_summary_tables
            ),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("success"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

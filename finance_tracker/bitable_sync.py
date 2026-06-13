import argparse
import datetime
import json

try:
    import lark_oapi as lark
    from lark_oapi.api.bitable.v1 import (
        AppTableRecord,
        Condition,
        CreateAppTableRecordRequest,
        DeleteAppTableRecordRequest,
        FilterInfo,
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


def auto_sync_enabled():
    config = get_feishu_config()
    return bool(config.bitable_sync_enabled and config.auto_sync and config.bitable_ready)


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
        FIELD_MAP["source_message_id"]: str(transaction.get("source_message_id") or ""),
        FIELD_MAP["created_at"]: _datetime_to_milliseconds(transaction.get("created_at")),
        FIELD_MAP["updated_at"]: _datetime_to_milliseconds(transaction.get("updated_at")),
        FIELD_MAP["status"]: str(transaction.get("status") or "active"),
        FIELD_MAP["deleted_at"]: _datetime_to_milliseconds(transaction.get("deleted_at")),
        FIELD_MAP["deleted_by_open_id"]: str(transaction.get("deleted_by_open_id") or ""),
        FIELD_MAP["delete_reason"]: str(transaction.get("delete_reason") or ""),
    }


class BitableSyncService:
    def __init__(self, client=None, config=None):
        self.config = config or get_feishu_config()
        self.feishu = FeishuClient(client=client, config=self.config)
        self.client = self.feishu.client

    def create_record(self, transaction):
        record = AppTableRecord.builder().fields(transaction_to_bitable_fields(transaction)).build()
        request = (
            CreateAppTableRecordRequest.builder()
            .app_token(self.config.bitable_app_token)
            .table_id(self.config.bitable_table_id)
            .request_body(record)
            .build()
        )
        result = response_result(self.client.bitable.v1.app_table_record.create(request))
        if result["success"] and result["data"]:
            created = getattr(result["data"], "record", None)
            result["record_id"] = getattr(created, "record_id", None)
        return result

    def update_record(self, record_id, transaction):
        record = AppTableRecord.builder().fields(transaction_to_bitable_fields(transaction)).build()
        request = (
            UpdateAppTableRecordRequest.builder()
            .app_token(self.config.bitable_app_token)
            .table_id(self.config.bitable_table_id)
            .record_id(record_id)
            .request_body(record)
            .build()
        )
        result = response_result(self.client.bitable.v1.app_table_record.update(request))
        result["record_id"] = record_id
        return result

    def delete_record(self, record_id):
        request = (
            DeleteAppTableRecordRequest.builder()
            .app_token(self.config.bitable_app_token)
            .table_id(self.config.bitable_table_id)
            .record_id(record_id)
            .build()
        )
        result = response_result(self.client.bitable.v1.app_table_record.delete(request))
        result["record_id"] = record_id
        return result

    def find_record_id(self, transaction_uid):
        condition = (
            Condition.builder()
            .field_name(FIELD_MAP["transaction_uid"])
            .operator("is")
            .value([str(transaction_uid)])
            .build()
        )
        filter_info = FilterInfo.builder().conjunction("and").conditions([condition]).build()
        body = SearchAppTableRecordRequestBody.builder().filter(filter_info).build()
        request = (
            SearchAppTableRecordRequest.builder()
            .app_token(self.config.bitable_app_token)
            .table_id(self.config.bitable_table_id)
            .page_size(1)
            .request_body(body)
            .build()
        )
        response = self.client.bitable.v1.app_table_record.search(request)
        result = response_result(response)
        if not result["success"] or not result["data"]:
            return None
        items = getattr(result["data"], "items", None) or []
        return getattr(items[0], "record_id", None) if items else None


def sync_transaction(transaction_uid, operation=None, service=None):
    init_db()
    config = service.config if service is not None else get_feishu_config()
    if not config.bitable_sync_enabled:
        return {"success": False, "message": "FEISHU_BITABLE_SYNC_ENABLED=false"}
    if not config.bitable_ready:
        return {"success": False, "message": "飞书多维表格配置不完整。"}
    service = service or BitableSyncService(config=config)

    with connect() as conn:
        row = conn.execute(
            """
            SELECT rowid, id, date, type, category, amount, description, created_at,
                   tags, is_need, is_fixed, transaction_uid, source, source_message_id,
                   feishu_record_id, updated_at, sync_status, sync_error,
                   source_user_open_id, source_chat_id, deleted_at,
                   deleted_by_open_id, delete_reason, status
            FROM transactions
            WHERE transaction_uid = ?
            """,
            (transaction_uid,),
        ).fetchone()
        columns = [
            "_rowid", "id", "date", "type", "category", "amount", "description",
            "created_at", "tags", "is_need", "is_fixed", "transaction_uid", "source",
            "source_message_id", "feishu_record_id", "updated_at", "sync_status", "sync_error",
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
            result = {"success": False, "message": "本地流水不存在。"}
        else:
            record_id = transaction.get("feishu_record_id") or service.find_record_id(transaction_uid)
            result = (
                service.update_record(record_id, transaction)
                if record_id
                else service.create_record(transaction)
            )
            if result.get("record_id"):
                record_id = result["record_id"]

        _finish_sync(
            transaction_uid,
            outbox_id,
            success=bool(result.get("success")),
            error=result.get("message", ""),
            record_id=locals().get("record_id"),
        )
        return result
    except Exception as exc:
        _finish_sync(transaction_uid, outbox_id, success=False, error=str(exc))
        return {"success": False, "message": str(exc)}


def sync_pending_transactions(limit=100, service=None):
    init_db()
    config = get_feishu_config()
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
    results = [
        sync_transaction(uid, operation=operation, service=service)
        for uid, operation in rows
    ]
    return {
        "success": all(item.get("success") for item in results) if results else True,
        "processed": len(results),
        "succeeded": sum(bool(item.get("success")) for item in results),
        "failed": sum(not item.get("success") for item in results),
        "message": f"同步完成：成功 {sum(bool(item.get('success')) for item in results)}，失败 {sum(not item.get('success') for item in results)}。",
    }


def full_sync(service=None):
    init_db()
    with connect() as conn:
        transaction_uids = [
            row[0]
            for row in conn.execute(
                "SELECT transaction_uid FROM transactions ORDER BY rowid ASC"
            ).fetchall()
        ]
    results = [sync_transaction(uid, operation="update", service=service) for uid in transaction_uids]
    return {
        "success": all(item.get("success") for item in results) if results else True,
        "processed": len(results),
        "succeeded": sum(bool(item.get("success")) for item in results),
        "failed": sum(not item.get("success") for item in results),
        "message": f"全量同步完成：成功 {sum(bool(item.get('success')) for item in results)}，失败 {sum(not item.get('success') for item in results)}。",
    }


def _finish_sync(transaction_uid, outbox_id, success, error="", record_id=None):
    status = "synced" if success else "failed"
    with connect() as conn:
        if success:
            conn.execute(
                """
                UPDATE sync_outbox
                SET status = 'done', last_error = '', updated_at = CURRENT_TIMESTAMP
                WHERE transaction_uid = ? AND status IN ('pending', 'failed')
                """,
                (transaction_uid,),
            )
        elif outbox_id:
            conn.execute(
                """
                UPDATE sync_outbox
                SET status = ?, retry_count = retry_count + ?, last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                ("done" if success else "failed", 0 if success else 1, "" if success else error[:1000], outbox_id),
            )
        conn.execute(
            """
            UPDATE transactions
            SET sync_status = ?, sync_error = ?, feishu_record_id = COALESCE(?, feishu_record_id),
                updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP)
            WHERE transaction_uid = ?
            """,
            (status, "" if success else error[:1000], record_id, transaction_uid),
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
        return [str(item).strip() for item in value if str(item).strip()]
    return [
        item.strip()
        for item in str(value or "").replace("，", ",").split(",")
        if item.strip()
    ]


def main():
    parser = argparse.ArgumentParser(description="Sync finance transactions to Feishu Bitable.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pending", action="store_true")
    group.add_argument("--full", action="store_true")
    args = parser.parse_args()
    result = sync_pending_transactions() if args.pending else full_sync()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

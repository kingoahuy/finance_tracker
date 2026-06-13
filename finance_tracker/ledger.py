import datetime
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path

import pandas as pd

try:
    from .config import PROJECT_ROOT, load_env_file
except ImportError:
    from config import PROJECT_ROOT, load_env_file

load_env_file()
DB_FILE = Path(os.getenv("FINANCE_DB_FILE", PROJECT_ROOT / "my_account_book.db"))
MONTHLY_BUDGET = int(os.getenv("FINANCE_MONTHLY_BUDGET", "2000"))

CATEGORIES = [
    "餐饮",
    "交通",
    "购物",
    "娱乐",
    "居住",
    "医疗",
    "教育",
    "人情",
    "工资",
    "奖金",
    "兼职",
    "理财",
    "退款",
    "退税",
    "报销",
    "红包",
    "其他",
]

SPECIAL_TAG_RULES = [
    {
        "tag": "2026海南旅游",
        "title": "2026 海南旅游",
        "start_date": "2026-05-30",
        "end_date": "2026-06-05",
        "report_from": "2026-06-05",
        "type": "支出",
    }
]

INCOME_KEYWORDS = {
    "工资": "工资",
    "奖金": "奖金",
    "兼职": "兼职",
    "理财": "理财",
    "退税": "退税",
    "退款": "退款",
    "退": "退款",
    "报销": "报销",
    "红包": "红包",
    "收到": "其他",
    "入账": "其他",
    "转入": "其他",
    "赚": "其他",
}

EXPENSE_KEYWORDS = {
    "水果": "餐饮",
    "饭": "餐饮",
    "咖喱": "餐饮",
    "餐": "餐饮",
    "食堂": "餐饮",
    "咖啡": "餐饮",
    "奶茶": "餐饮",
    "外卖": "餐饮",
    "早餐": "餐饮",
    "午餐": "餐饮",
    "晚餐": "餐饮",
    "地铁": "交通",
    "公交": "交通",
    "打车": "交通",
    "出租": "交通",
    "机场大巴": "交通",
    "大巴": "交通",
    "机场": "交通",
    "高铁": "交通",
    "火车": "交通",
    "机票": "交通",
    "油": "交通",
    "买": "购物",
    "购物": "购物",
    "手机": "购物",
    "拖鞋": "购物",
    "迪卡侬": "购物",
    "衣服": "购物",
    "超市": "购物",
    "电影": "娱乐",
    "游戏": "娱乐",
    "唱歌": "娱乐",
    "娱乐": "娱乐",
    "房租": "居住",
    "酒店": "居住",
    "住宿": "居住",
    "水电": "居住",
    "电费": "居住",
    "水费": "居住",
    "燃气": "居住",
    "物业": "居住",
    "宽带": "居住",
    "药": "医疗",
    "医院": "医疗",
    "医疗": "医疗",
    "打印": "教育",
    "书": "教育",
    "课程": "教育",
    "论文": "教育",
    "礼": "人情",
    "请客": "人情",
    "icloud": "其他",
    "iCloud": "其他",
    "云空间": "其他",
}

EXPENSE_PRIORITY_KEYWORDS = [
    ("水电", "居住"),
    ("电费", "居住"),
    ("水费", "居住"),
    ("别墅", "居住"),
    ("酒店", "居住"),
    ("住宿", "居住"),
    ("一晚", "居住"),
    ("第二晚", "居住"),
    ("机票", "交通"),
    ("租车", "交通"),
    ("租电动车", "交通"),
    ("电动车", "交通"),
    ("校车", "交通"),
    ("观光车", "交通"),
    ("机场大巴", "交通"),
    ("门票", "娱乐"),
    ("西岛", "娱乐"),
    ("分界洲", "娱乐"),
    ("浆板", "娱乐"),
    ("椰梦长廊", "娱乐"),
    ("椰子鸡", "餐饮"),
    ("糟粕醋", "餐饮"),
    ("炒饭", "餐饮"),
    ("清补凉", "餐饮"),
    ("瓜子", "餐饮"),
    ("炸炸", "餐饮"),
    ("生蚝", "餐饮"),
    ("小吃", "餐饮"),
    ("饮料", "餐饮"),
    ("西瓜汁", "餐饮"),
    ("西瓜", "餐饮"),
    ("宵夜", "餐饮"),
    ("水", "餐饮"),
    ("便利店", "购物"),
    ("超市", "购物"),
    ("耳环", "购物"),
    ("戒指", "购物"),
]

NEED_CATEGORIES = {"餐饮", "交通", "居住", "医疗", "教育"}
FIXED_KEYWORDS = ("房租", "水电", "电费", "水费", "燃气", "物业", "宽带", "会员", "订阅", "月租", "icloud", "iCloud")
AMOUNT_RE = re.compile(r"(?<!\d)(?:¥|￥)?\s*(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?")
DATE_RE = re.compile(r"(?:(\d{4})[-/年])?(\d{1,2})[月/-](\d{1,2})日?")
RELATIVE_DATE_RE = re.compile(r"(前天|昨天|今天)")
BOUNDARY_RE = re.compile(r"[\s。.!！?？]+")
LEADING_NOISE_RE = re.compile(
    r"^(?:又|然后|还有|顺便|今天|昨天|前天|早上|上午|中午|下午|晚上|夜里|凌晨|去|在|到|了)+"
)
LEADING_ACTION_RE = re.compile(
    r"^(?:花了|花|买了|买|付了|付|支付了|支付|交了|交|缴了|缴|吃了|吃|喝了|喝|住了|住|订阅了|订阅|开通了|开通|充了|充|用了|用|买的)+"
)
PAYMENT_SPLIT_RE = re.compile(r"(?:花了|花|付了|付|支付了|支付|交了|交|缴了|缴)")


class FinanceConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=10, factory=FinanceConnection)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                type TEXT,
                category TEXT,
                amount REAL,
                description TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                tags TEXT,
                is_need INTEGER DEFAULT 0,
                is_fixed INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date TEXT,
                schedule_time TEXT,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _ensure_column(conn, "transactions", "created_at", "TEXT")
        _ensure_column(conn, "transactions", "tags", "TEXT")
        _ensure_column(conn, "transactions", "is_need", "INTEGER DEFAULT 0")
        _ensure_column(conn, "transactions", "is_fixed", "INTEGER DEFAULT 0")
        _ensure_column(conn, "transactions", "transaction_uid", "TEXT")
        _ensure_column(conn, "transactions", "source", "TEXT DEFAULT 'streamlit'")
        _ensure_column(conn, "transactions", "source_message_id", "TEXT")
        _ensure_column(conn, "transactions", "feishu_record_id", "TEXT")
        _ensure_column(conn, "transactions", "updated_at", "TEXT")
        _ensure_column(conn, "transactions", "sync_status", "TEXT DEFAULT 'pending'")
        _ensure_column(conn, "transactions", "sync_error", "TEXT")
        _ensure_column(conn, "transactions", "source_user_open_id", "TEXT")
        _ensure_column(conn, "transactions", "source_chat_id", "TEXT")
        _ensure_column(conn, "transactions", "deleted_at", "TEXT")
        _ensure_column(conn, "transactions", "deleted_by_open_id", "TEXT")
        _ensure_column(conn, "transactions", "delete_reason", "TEXT")
        _ensure_column(conn, "transactions", "status", "TEXT DEFAULT 'active'")
        _ensure_column(conn, "email_jobs", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_events (
                event_id TEXT PRIMARY KEY,
                message_id TEXT,
                sender_open_id TEXT,
                status TEXT,
                processed_at TEXT,
                response_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_uid TEXT NOT NULL,
                operation TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_actions (
                action_id TEXT PRIMARY KEY,
                intent TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                sender_open_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                source_message_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                result_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT NOT NULL,
                resolved_at TEXT
            )
            """
        )
        conn.execute("UPDATE transactions SET id = rowid WHERE id IS NULL")
        conn.execute("UPDATE transactions SET source = 'streamlit' WHERE source IS NULL OR source = ''")
        conn.execute("UPDATE transactions SET sync_status = 'pending' WHERE sync_status IS NULL OR sync_status = ''")
        conn.execute("UPDATE transactions SET status = 'active' WHERE status IS NULL OR status = ''")
        conn.execute("UPDATE transactions SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)")
        missing_uids = conn.execute(
            "SELECT rowid FROM transactions WHERE transaction_uid IS NULL OR transaction_uid = ''"
        ).fetchall()
        for (rowid,) in missing_uids:
            conn.execute(
                "UPDATE transactions SET transaction_uid = ? WHERE rowid = ?",
                (str(uuid.uuid4()), rowid),
            )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_uid ON transactions(transaction_uid)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_transactions_source_message ON transactions(source_message_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sync_outbox_status ON sync_outbox(status, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_processed_events_message ON processed_events(message_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_transactions_owner_status "
            "ON transactions(source_user_open_id, source_chat_id, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_actions_status_expiry "
            "ON pending_actions(status, expires_at)"
        )


def _ensure_column(conn, table, column, definition):
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def add_transaction(data):
    init_db()
    normalized = normalize_transaction(data)
    transaction_uid = str(data.get("transaction_uid") or uuid.uuid4())
    source = str(data.get("source") or "streamlit")
    source_message_id = data.get("source_message_id")
    source_user_open_id = data.get("source_user_open_id")
    source_chat_id = data.get("source_chat_id")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO transactions
                (date, type, category, amount, description, tags, is_need, is_fixed,
                 transaction_uid, source, source_message_id, source_user_open_id,
                 source_chat_id, updated_at, sync_status, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'active')
            """,
            (
                normalized["date"],
                normalized["type"],
                normalized["category"],
                normalized["amount"],
                normalized["description"],
                normalized["tags"],
                normalized["is_need"],
                normalized["is_fixed"],
                transaction_uid,
                source,
                source_message_id,
                source_user_open_id,
                source_chat_id,
                now,
            ),
        )
        transaction_id = cursor.lastrowid
        conn.execute(
            "UPDATE transactions SET id = COALESCE(id, ?) WHERE rowid = ?",
            (transaction_id, transaction_id),
        )
        _enqueue_sync(conn, transaction_uid, "create")

    return {
        "id": int(transaction_id),
        "transaction_uid": transaction_uid,
        **normalized,
        "source": source,
        "source_message_id": source_message_id,
        "source_user_open_id": source_user_open_id,
        "source_chat_id": source_chat_id,
        "status": "active",
        "sync_status": "pending",
    }


def _enqueue_sync(conn, transaction_uid, operation):
    conn.execute(
        """
        INSERT INTO sync_outbox
            (transaction_uid, operation, status, retry_count, updated_at)
        VALUES (?, ?, 'pending', 0, CURRENT_TIMESTAMP)
        """,
        (transaction_uid, operation),
    )


def normalize_transaction(data):
    date_value = data.get("date") or datetime.date.today().isoformat()
    if isinstance(date_value, datetime.datetime):
        date_value = date_value.date()
    if isinstance(date_value, datetime.date):
        date_value = date_value.strftime("%Y-%m-%d")

    txn_type = data.get("type", "支出")
    category = data.get("category", "其他")
    if category not in CATEGORIES:
        category = "其他"

    description = str(data.get("description", ""))
    is_income = txn_type == "收入"
    default_need = int(not is_income and category in NEED_CATEGORIES)
    default_fixed = int(any(keyword in description for keyword in FIXED_KEYWORDS))
    tags = merge_tags(data.get("tags", ""), auto_tags_for_transaction(str(date_value), txn_type, description))

    return {
        "date": str(date_value),
        "type": "收入" if is_income else "支出",
        "category": category,
        "amount": float(data.get("amount", 0) or 0),
        "description": description,
        "tags": tags,
        "is_need": int(bool(data.get("is_need", default_need))),
        "is_fixed": int(bool(data.get("is_fixed", default_fixed))),
    }


def add_email_job(report_date, schedule_time):
    init_db()
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO email_jobs (report_date, schedule_time, status) VALUES (?, ?, ?)",
            (str(report_date), str(schedule_time), "pending"),
        )
        return cursor.lastrowid


def load_email_jobs(status=None, limit=30):
    init_db()
    where = ""
    params = []
    if status:
        where = "WHERE status = ?"
        params.append(status)

    with connect() as conn:
        return pd.read_sql(
            f"""
            SELECT *
            FROM email_jobs
            {where}
            ORDER BY schedule_time DESC, id DESC
            LIMIT ?
            """,
            conn,
            params=[*params, int(limit)],
        )


def get_pending_jobs():
    init_db()
    with connect() as conn:
        try:
            return pd.read_sql(
                "SELECT * FROM email_jobs WHERE status='pending' ORDER BY schedule_time ASC",
                conn,
            )
        except sqlite3.Error:
            return pd.DataFrame()


def delete_job(job_id):
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM email_jobs WHERE id = ?", (int(job_id),))


def update_email_job_status(job_id, status):
    init_db()
    with connect() as conn:
        conn.execute("UPDATE email_jobs SET status = ? WHERE id = ?", (status, int(job_id)))


def load_transactions(include_deleted=False):
    init_db()
    with connect() as conn:
        try:
            df = pd.read_sql(
                """
                SELECT
                    rowid AS _rowid,
                    id,
                    date,
                    type,
                    category,
                    amount,
                    description,
                    created_at,
                    tags,
                    is_need,
                    is_fixed,
                    transaction_uid,
                    source,
                    source_message_id,
                    feishu_record_id,
                    updated_at,
                    sync_status,
                    sync_error,
                    source_user_open_id,
                    source_chat_id,
                    deleted_at,
                    deleted_by_open_id,
                    delete_reason,
                    status
                FROM transactions
                WHERE (? = 1 OR status = 'active')
                ORDER BY date DESC, rowid DESC
                """,
                conn,
                params=[int(bool(include_deleted))],
            )
        except sqlite3.Error:
            return pd.DataFrame()

    if df.empty:
        return df

    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(df["_rowid"]).astype(int)
    df["tags"] = df["tags"].fillna("")
    df["is_need"] = pd.to_numeric(df["is_need"], errors="coerce").fillna(0).astype(int)
    df["is_fixed"] = pd.to_numeric(df["is_fixed"], errors="coerce").fillna(0).astype(int)
    df["source"] = df["source"].fillna("streamlit")
    df["sync_status"] = df["sync_status"].fillna("pending")
    df["status"] = df["status"].fillna("active")
    return df


def update_transactions_from_editor(edited_df, original_rowids=None):
    init_db()
    if edited_df is None:
        return {"updated": 0, "created": 0, "deleted": 0}

    save_df = edited_df.copy()
    if "_rowid" not in save_df.columns:
        raise ValueError("编辑数据缺少内部记录标识，请刷新页面后重试。")
    original_rowids = {
        int(value)
        for value in (original_rowids or [])
        if value is not None and str(value).strip()
    }
    if not original_rowids:
        original_rowids = {
            int(value)
            for value in save_df["_rowid"].dropna().tolist()
            if str(value).strip()
        }
    if not save_df.empty:
        save_df["date"] = pd.to_datetime(
            save_df["date"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
        save_df["type"] = save_df["type"].fillna("支出")
        save_df["category"] = save_df["category"].fillna("其他")
        save_df["amount"] = pd.to_numeric(
            save_df["amount"], errors="coerce"
        ).fillna(0.0)
        save_df["description"] = save_df["description"].fillna("").astype(str)
        save_df["tags"] = save_df["tags"].fillna("").astype(str)
        save_df["is_need"] = save_df["is_need"].fillna(0).astype(int)
        save_df["is_fixed"] = save_df["is_fixed"].fillna(0).astype(int)

    result = {"updated": 0, "created": 0, "deleted": 0}
    with connect() as conn:
        current_rows = {
            row[0]: {
                "transaction_uid": row[1],
                "date": row[2],
                "type": row[3],
                "category": row[4],
                "amount": float(row[5] or 0),
                "description": row[6] or "",
                "tags": row[7] or "",
                "is_need": int(row[8] or 0),
                "is_fixed": int(row[9] or 0),
            }
            for row in conn.execute(
                """
                SELECT rowid, transaction_uid, date, type, category, amount,
                       description, tags, is_need, is_fixed
                FROM transactions WHERE status = 'active'
                """
            )
            if row[0] in original_rowids
        }
        current_rowids = set(current_rows)
        edited_rowids = {
            int(value)
            for value in save_df["_rowid"].dropna().tolist()
            if str(value).strip()
        }

        for rowid in current_rowids - edited_rowids:
            conn.execute(
                """
                UPDATE transactions
                SET status = 'deleted', deleted_at = CURRENT_TIMESTAMP,
                    delete_reason = 'streamlit editor', updated_at = CURRENT_TIMESTAMP,
                    sync_status = 'pending'
                WHERE rowid = ? AND status = 'active'
                """,
                (rowid,),
            )
            _enqueue_sync(conn, current_rows[rowid]["transaction_uid"], "update")
            result["deleted"] += 1

        for _, row in save_df.iterrows():
            rowid = row.get("_rowid")
            values = (
                row["date"],
                row["type"],
                row["category"],
                float(row["amount"]),
                row["description"],
                row["tags"],
                int(row["is_need"]),
                int(row["is_fixed"]),
            )
            if pd.notna(rowid) and int(rowid) in current_rowids:
                current = current_rows[int(rowid)]
                if values == (
                    current["date"],
                    current["type"],
                    current["category"],
                    current["amount"],
                    current["description"],
                    current["tags"],
                    current["is_need"],
                    current["is_fixed"],
                ):
                    continue
                transaction_uid = current["transaction_uid"]
                conn.execute(
                    """
                    UPDATE transactions
                    SET date = ?,
                        type = ?,
                        category = ?,
                        amount = ?,
                        description = ?,
                        tags = ?,
                        is_need = ?,
                        is_fixed = ?,
                        updated_at = CURRENT_TIMESTAMP,
                        sync_status = 'pending',
                        sync_error = ''
                    WHERE rowid = ?
                      AND status = 'active'
                    """,
                    (*values, int(rowid)),
                )
                _enqueue_sync(conn, transaction_uid, "update")
                result["updated"] += 1
            elif row["date"]:
                transaction_uid = str(uuid.uuid4())
                cursor = conn.execute(
                    """
                    INSERT INTO transactions
                        (date, type, category, amount, description, tags, is_need, is_fixed,
                         transaction_uid, source, updated_at, sync_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'streamlit', CURRENT_TIMESTAMP, 'pending')
                    """,
                    (*values, transaction_uid),
                )
                conn.execute(
                    "UPDATE transactions SET id = COALESCE(id, ?) WHERE rowid = ?",
                    (cursor.lastrowid, cursor.lastrowid),
                )
                _enqueue_sync(conn, transaction_uid, "create")
                result["created"] += 1
    return result


def create_pending_action(
    intent,
    payload,
    sender_open_id,
    chat_id,
    source_message_id=None,
    ttl_minutes=10,
):
    init_db()
    action_id = str(uuid.uuid4())
    expires_at = (
        datetime.datetime.now(datetime.UTC)
        + datetime.timedelta(minutes=max(1, int(ttl_minutes)))
    ).strftime("%Y-%m-%d %H:%M:%S")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO pending_actions
                (action_id, intent, payload_json, sender_open_id, chat_id,
                 source_message_id, status, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                action_id,
                str(intent),
                json.dumps(payload, ensure_ascii=False),
                str(sender_open_id or ""),
                str(chat_id or ""),
                source_message_id,
                expires_at,
            ),
        )
    return get_pending_action(action_id)


def get_pending_action(action_id):
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT action_id, intent, payload_json, sender_open_id, chat_id,
                   source_message_id, status, result_json, created_at, expires_at,
                   resolved_at
            FROM pending_actions WHERE action_id = ?
            """,
            (str(action_id),),
        ).fetchone()
    if not row:
        return None
    keys = [
        "action_id", "intent", "payload_json", "sender_open_id", "chat_id",
        "source_message_id", "status", "result_json", "created_at", "expires_at",
        "resolved_at",
    ]
    action = dict(zip(keys, row))
    action["payload"] = json.loads(action.pop("payload_json") or "{}")
    action["result"] = json.loads(action.pop("result_json") or "null")
    return action


def resolve_pending_action(action_id, status, result=None):
    if status not in {"confirmed", "cancelled", "expired", "failed"}:
        raise ValueError("Invalid pending action status.")
    init_db()
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE pending_actions
            SET status = ?, result_json = ?, resolved_at = CURRENT_TIMESTAMP
            WHERE action_id = ? AND status IN ('pending', 'processing')
            """,
            (
                status,
                json.dumps(result, ensure_ascii=False) if result is not None else None,
                str(action_id),
            ),
        )
    return cursor.rowcount == 1


def claim_pending_action(action_id):
    init_db()
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE pending_actions
            SET status = 'processing'
            WHERE action_id = ? AND status = 'pending' AND expires_at > CURRENT_TIMESTAMP
            """,
            (str(action_id),),
        )
    return cursor.rowcount == 1


def expire_pending_actions(now=None):
    init_db()
    now_text = (
        now.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(now, datetime.datetime)
        else str(
            now
            or datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
        )
    )
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE pending_actions
            SET status = 'expired', resolved_at = CURRENT_TIMESTAMP
            WHERE status = 'pending' AND expires_at <= ?
            """,
            (now_text,),
        )
    return cursor.rowcount


def parse_entry_text(text, default_date=None):
    base_date = _coerce_date(default_date or datetime.date.today())
    parts = split_date_chunks(text or "", base_date)
    records = []
    for entry_date, part in parts:
        records.extend(parse_amount_entries(part, entry_date))
    return records


def split_date_chunks(text, base_date):
    tokens = []
    for match in DATE_RE.finditer(text):
        year_text, month_text, day_text = match.groups()
        year = int(year_text) if year_text else base_date.year
        entry_date = datetime.date(year, int(month_text), int(day_text)).strftime("%Y-%m-%d")
        tokens.append((match.start(), match.end(), entry_date))

    for match in RELATIVE_DATE_RE.finditer(text):
        marker = match.group(1)
        if marker == "前天":
            entry_date = base_date - datetime.timedelta(days=2)
        elif marker == "昨天":
            entry_date = base_date - datetime.timedelta(days=1)
        else:
            entry_date = base_date
        tokens.append((match.start(), match.end(), entry_date.strftime("%Y-%m-%d")))

    tokens.sort(key=lambda item: item[0])
    current_date = base_date.strftime("%Y-%m-%d")
    chunks = []
    pos = 0

    for start, end, entry_date in tokens:
        if start > pos:
            chunks.append((current_date, text[pos:start]))
        current_date = entry_date
        pos = end

    if pos < len(text):
        chunks.append((current_date, text[pos:]))

    if not chunks and text.strip():
        chunks.append((current_date, text))
    return [(date, chunk.strip()) for date, chunk in chunks if chunk.strip()]


def parse_amount_entries(text, entry_date):
    records = []
    amount_matches = list(AMOUNT_RE.finditer(text))
    prev_end = 0

    for match in amount_matches:
        raw_candidate = text[prev_end:match.start()]
        prev_end = match.end()
        description = clean_description(raw_candidate)
        if not description:
            continue

        amount = float(match.group(1))
        txn_type, category = classify_text(description)
        auto_tags = auto_tags_for_transaction(entry_date, txn_type, description)
        records.append(
            {
                "date": entry_date,
                "type": txn_type,
                "category": category,
                "amount": amount,
                "description": description,
                "tags": ",".join(auto_tags),
                "is_need": int(txn_type == "支出" and category in NEED_CATEGORIES),
                "is_fixed": int(any(keyword.lower() in description.lower() for keyword in FIXED_KEYWORDS)),
                "local_comment": "已按本地规则识别，未调用外部 AI。",
            }
        )
    return records


def clean_description(text):
    value = text.strip(" \t\r\n，,。.；;：:")
    if not value:
        return ""

    boundary_parts = [part for part in BOUNDARY_RE.split(value) if part.strip()]
    if boundary_parts:
        value = boundary_parts[-1].strip(" ，,。.；;：:")

    payment_parts = [part for part in PAYMENT_SPLIT_RE.split(value) if part.strip()]
    if payment_parts:
        value = payment_parts[-1].strip(" ，,。.；;：:")

    value = trim_utility_description(value)
    previous = None
    while previous != value:
        previous = value
        value = LEADING_NOISE_RE.sub("", value)
        value = LEADING_ACTION_RE.sub("", value)
        value = value.strip(" 的了，,。.；;：:")

    return value


def trim_utility_description(text):
    for keyword in ("电费", "水费", "燃气费", "燃气", "水电费", "水电"):
        if keyword in text:
            match = re.search(rf"([\u4e00-\u9fffA-Za-z0-9]{{0,4}}{keyword})", text)
            if match:
                return match.group(1).lstrip("了的")
    return text


def extract_entry_date(text, default_date):
    base_date = _coerce_date(default_date)
    clean_text = text

    if "前天" in clean_text:
        clean_text = clean_text.replace("前天", "")
        return (base_date - datetime.timedelta(days=2)).strftime("%Y-%m-%d"), clean_text
    if "昨天" in clean_text:
        clean_text = clean_text.replace("昨天", "")
        return (base_date - datetime.timedelta(days=1)).strftime("%Y-%m-%d"), clean_text
    if "今天" in clean_text:
        clean_text = clean_text.replace("今天", "")
        return base_date.strftime("%Y-%m-%d"), clean_text

    match = DATE_RE.search(clean_text)
    if match:
        year_text, month_text, day_text = match.groups()
        year = int(year_text) if year_text else base_date.year
        parsed = datetime.date(year, int(month_text), int(day_text))
        clean_text = DATE_RE.sub("", clean_text, count=1)
        return parsed.strftime("%Y-%m-%d"), clean_text

    return base_date.strftime("%Y-%m-%d"), clean_text


def _coerce_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.datetime.strptime(str(value), "%Y-%m-%d").date()


def classify_text(text):
    lower_text = text.lower()
    for keyword, category in INCOME_KEYWORDS.items():
        if keyword.lower() in lower_text:
            return "收入", category
    for keyword, category in EXPENSE_PRIORITY_KEYWORDS:
        if keyword.lower() in lower_text:
            return "支出", category
    for keyword, category in EXPENSE_KEYWORDS.items():
        if keyword.lower() in lower_text:
            return "支出", category
    return "支出", "其他"


def auto_tags_for_transaction(date_value, txn_type, description=""):
    try:
        txn_date = _coerce_date(date_value)
    except Exception:
        return []

    tags = []
    for rule in SPECIAL_TAG_RULES:
        if rule.get("type") and rule["type"] != txn_type:
            continue
        start_date = _coerce_date(rule["start_date"])
        end_date = _coerce_date(rule["end_date"])
        if start_date <= txn_date <= end_date:
            tags.append(rule["tag"])
            continue
        if rule["tag"] in str(description):
            tags.append(rule["tag"])
    return tags


def merge_tags(existing_tags, new_tags):
    tags = []
    if isinstance(existing_tags, list):
        raw_tags = existing_tags
    else:
        raw_tags = str(existing_tags or "").replace("，", ",").split(",")

    for tag in [*raw_tags, *new_tags]:
        clean_tag = str(tag).strip()
        if clean_tag and clean_tag not in tags:
            tags.append(clean_tag)
    return ",".join(tags)

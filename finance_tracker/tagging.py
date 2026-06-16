import argparse
import datetime
import json
import re


MAX_TAGS = 5
INCOME_TAGS = {"工资", "奖金", "报销", "退款", "理财", "红包"}

SCENE_RULES = (
    ("食堂", ("食堂",)),
    ("外卖", ("外卖", "美团外卖", "饿了么")),
    ("聚餐", ("聚餐", "请客", "宴请")),
    ("咖啡", ("咖啡", "拿铁", "美式")),
    ("奶茶", ("奶茶",)),
    ("超市", ("超市", "便利店")),
    ("打车", ("打车", "出租车", "网约车", "滴滴")),
    ("通勤", ("地铁", "公交", "通勤", "上班", "下班")),
    ("住宿", ("住宿", "酒店", "宾馆", "民宿", "别墅")),
    ("居住", ("房租", "租房", "物业", "水电", "电费", "水费", "燃气")),
    ("门票", ("门票", "入场券")),
    ("订阅", ("订阅", "会员", "icloud", "云空间", "月费")),
    ("医疗", ("医院", "门诊", "看病", "药", "医疗")),
    ("学习", ("学习", "课程", "书", "打印", "论文", "培训")),
    ("人情", ("红包", "礼物", "礼金", "人情", "请客")),
)

PROJECT_RULES = (
    ("北京生活", ("北京",)),
    ("入职准备", ("入职", "面试", "工牌", "体检")),
    ("数码设备", ("手机", "电脑", "平板", "耳机", "相机", "数码")),
    ("日常通勤", ("地铁", "公交", "通勤", "上班", "下班")),
    ("租房居住", ("房租", "租房", "物业", "水电", "电费", "水费", "燃气")),
)

MEAL_RULES = (
    ("早餐", ("早餐", "早饭")),
    ("午餐", ("午餐", "午饭", "中午")),
    ("晚餐", ("晚餐", "晚饭", "晚上吃饭", "下午在食堂")),
    ("夜宵", ("夜宵", "宵夜")),
)

CATEGORY_FALLBACKS = {
    "餐饮": "餐饮",
    "交通": "交通",
    "购物": "购物",
    "娱乐": "娱乐",
    "居住": "居住",
    "医疗": "医疗",
    "教育": "学习",
    "人情": "人情",
    "工资": "工资",
    "奖金": "奖金",
    "理财": "理财",
    "退款": "退款",
    "报销": "报销",
    "红包": "红包",
    "兼职": "兼职",
    "退税": "退税",
    "其他": "其他",
}


def clean_tags(value):
    """Return stable, deduplicated tag names from strings or iterables."""
    if isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = re.split(r"[,，、;；|]+", str(value or ""))

    result = []
    for item in values:
        tag = re.sub(r"\s+", " ", str(item or "")).strip(" ,，、;；|")
        if tag and tag not in result:
            result.append(tag)
    return result


def merge_tags(existing_tags, generated_tags, limit=MAX_TAGS):
    """Keep caller-provided tags first, then add useful local-rule tags."""
    result = []
    for tag in [*clean_tags(existing_tags), *clean_tags(generated_tags)]:
        if tag not in result:
            result.append(tag)
        if len(result) >= limit:
            break
    return result


def generate_tags(transaction, raw_text=None):
    """Generate one to five explainable tags for a normalized transaction."""
    transaction = dict(transaction or {})
    existing = clean_tags(transaction.get("tags"))
    description = str(transaction.get("description") or "")
    text = f"{description} {raw_text or ''}".lower()
    category = str(transaction.get("category") or "其他")
    txn_type = str(transaction.get("type") or "支出")
    amount = _as_amount(transaction.get("amount"))
    txn_date = _as_date(transaction.get("date"))
    is_need = _as_bool(transaction.get("is_need"))
    is_fixed = _as_bool(transaction.get("is_fixed"))

    generated = []

    if txn_type == "收入":
        income_tag = category if category in INCOME_TAGS else _first_match(
            text,
            ((tag, (tag,)) for tag in INCOME_TAGS),
        )
        if income_tag:
            generated.append(income_tag)
    else:
        for tag, keywords in SCENE_RULES:
            if _contains_any(text, keywords):
                generated.append(tag)

        if _is_hainan_trip(txn_date, text):
            generated.extend(["旅游", "2026海南旅游"])
        elif _contains_any(text, ("旅游", "旅行", "景区", "度假")):
            generated.append("旅游")

        generated.append(CATEGORY_FALLBACKS.get(category, category or "其他"))
        if is_fixed:
            generated.append("固定支出")
        generated.append("刚需" if is_need else "非刚需")

        for tag, keywords in MEAL_RULES:
            if _contains_any(text, keywords):
                generated.append(tag)

        for tag, keywords in PROJECT_RULES:
            if _contains_any(text, keywords):
                generated.append(tag)

        if not is_fixed:
            generated.append("变动支出")
        if amount >= 1000:
            generated.append("大额支出")
        elif 0 < amount <= 30:
            generated.append("小额高频")
        if txn_date:
            generated.append("周末" if txn_date.weekday() >= 5 else "工作日")

    fallback = CATEGORY_FALLBACKS.get(category, category or "其他")
    if not existing and not generated:
        generated.append(fallback)

    tags = merge_tags(existing, generated)
    if not tags:
        tags = [fallback or "其他"]
    return ",".join(tags[:MAX_TAGS])


def backfill_tags(apply=False, limit_examples=20):
    """Fill empty tags on active transactions without touching deleted rows."""
    from . import ledger

    ledger.init_db()
    with ledger.connect() as conn:
        rows = conn.execute(
            """
            SELECT rowid, id, transaction_uid, date, type, category, amount,
                   description, tags, is_need, is_fixed
            FROM transactions
            WHERE status = 'active'
              AND TRIM(COALESCE(tags, '')) = ''
            ORDER BY rowid
            """
        ).fetchall()

        planned = []
        for row in rows:
            tags = generate_tags(
                {
                    "date": row[3],
                    "type": row[4],
                    "category": row[5],
                    "amount": row[6],
                    "description": row[7],
                    "tags": row[8],
                    "is_need": row[9],
                    "is_fixed": row[10],
                }
            )
            planned.append(
                {
                    "rowid": int(row[0]),
                    "local_id": int(row[1] or row[0]),
                    "transaction_uid": str(row[2] or ""),
                    "date": str(row[3] or ""),
                    "category": str(row[5] or "其他"),
                    "amount": float(row[6] or 0),
                    "tags": tags,
                }
            )

        if apply:
            for item in planned:
                conn.execute(
                    """
                    UPDATE transactions
                    SET tags = ?, sync_status = 'pending', sync_error = '',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE rowid = ? AND status = 'active'
                      AND TRIM(COALESCE(tags, '')) = ''
                    """,
                    (item["tags"], item["rowid"]),
                )
                conn.execute(
                    """
                    INSERT INTO sync_outbox
                        (transaction_uid, operation, status, retry_count, updated_at)
                    VALUES (?, 'update', 'pending', 0, CURRENT_TIMESTAMP)
                    """,
                    (item["transaction_uid"],),
                )

    return {
        "success": True,
        "mode": "apply" if apply else "dry-run",
        "planned_count": len(planned),
        "updated_count": len(planned) if apply else 0,
        "examples": [
            {
                key: value
                for key, value in item.items()
                if key not in {"rowid", "transaction_uid"}
            }
            for item in planned[: max(0, int(limit_examples))]
        ],
    }


def _contains_any(text, keywords):
    return any(str(keyword).lower() in text for keyword in keywords)


def _first_match(text, rules):
    for tag, keywords in rules:
        if _contains_any(text, keywords):
            return tag
    return None


def _is_hainan_trip(txn_date, text):
    if _contains_any(text, ("海南", "三亚", "陵水", "万宁", "海口", "分界洲", "西岛")):
        return True
    return bool(
        txn_date
        and datetime.date(2026, 5, 30) <= txn_date <= datetime.date(2026, 6, 5)
    )


def _as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _as_amount(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "是"}
    return bool(value)


def main():
    parser = argparse.ArgumentParser(description="Generate and backfill transaction tags.")
    parser.add_argument("--backfill-tags", action="store_true", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            backfill_tags(apply=args.apply),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

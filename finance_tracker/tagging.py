import argparse
import datetime
import json
import re

try:
    from .advance_payment import (
        PERSONAL_ADVANCE_TAG,
        text_mentions_personal_advance,
    )
    from .meal_subsidy import (
        MEAL_SUBSIDY_USAGE_TAG,
        is_meal_subsidy_expense,
    )
except ImportError:
    from advance_payment import (
        PERSONAL_ADVANCE_TAG,
        text_mentions_personal_advance,
    )
    from meal_subsidy import (
        MEAL_SUBSIDY_USAGE_TAG,
        is_meal_subsidy_expense,
    )


# Every record receives three evidence-backed tags. Specific scenes and sources
# come first; stable category/need/fixed facts fill any remaining slots.
MAX_TAGS = 3
TRUSTED_EXISTING_TAGS = {PERSONAL_ADVANCE_TAG, "2026海南旅游"}

SCENE_RULES = (
    ("食堂", ("食堂",)),
    ("外卖", ("外卖", "美团外卖", "饿了么")),
    ("聚餐", ("聚餐", "宴请")),
    ("自助餐", ("自助餐",)),
    ("咖啡", ("咖啡", "拿铁", "美式")),
    ("奶茶", ("奶茶",)),
    ("乳制品", ("酸奶", "悦鲜活")),
    ("水果", ("水果", "西瓜", "香蕉", "蓝莓", "草莓", "葡萄", "青提", "脐橙")),
    ("零食小吃", ("零食", "小吃", "瓜子", "薯片", "臭豆腐", "锅盔", "牛肉饼",
                  "卤菜", "糖油粑粑", "雪糕", "饼干", "方便面", "香肠", "山楂", "麻花")),
    ("酒饮", ("喝酒", "啤酒", "白酒", "红酒")),
    ("饮品", ("饮料", "可乐", "喝茶", "矿泉水", "纯净水", "瓶装水")),
    ("烘焙面点", ("面包", "蛋糕", "花卷", "馒头", "罗森尼娜")),
    ("订阅", ("订阅", "会员", "vip", "icloud", "云空间", "月费")),
    ("快餐", ("快餐", "汉堡", "炸鸡")),
    ("正餐", ("吃饭", "烤肉饭", "咖喱饭", "炒饭", "炒面", "盖饭", "烧烤", "火锅",
              "饺子", "米线", "肉夹馍", "麦当劳", "大米先生", "大碗先生", "云饺",
              "寿司", "汆肉粉", "肉丝粉", "农家一碗香", "盛香亭", "椰子鸡", "糟粕醋")),
    ("超市", ("超市", "便利店")),
    ("洗浴", ("洗澡",)),
    ("洗衣", ("洗衣服", "洗衣")),
    ("理发", ("剪头发", "理发")),
    ("通讯充值", ("话费", "手机充值")),
    ("台球", ("台球",)),
    ("棋牌", ("麻将", "德州扑克")),
    ("运动健身", ("游泳", "健身", "网球", "健身房")),
    ("电影", ("看电影", "电影票")),
    ("游戏", ("游戏", "小丑牌", "杀戮高塔")),
    ("地铁", ("地铁",)),
    ("公交", ("公交", "校车")),
    ("打车", ("打车", "专车", "出租车", "网约车", "滴滴")),
    ("共享单车", ("骑车", "骑单车", "自行车", "共享单车")),
    ("交通罚款", ("交通罚款", "电车罚款", "违章罚款")),
    ("铁路出行", ("高铁", "火车")),
    ("日常出行", ("交通花费", "车票", "返程票")),
    ("物流快递", ("快递", "运费", "顺丰")),
    ("航空出行", ("机票", "机场大巴", "机场")),
    ("租车", ("租车", "租电动车", "电动车")),
    ("加油", ("加油", "油费")),
    ("停车", ("停车", "停车费")),
    ("房租", ("房租", "租房")),
    ("水电燃气", ("水电", "电费", "水费", "燃气")),
    ("物业", ("物业",)),
    ("宽带", ("宽带",)),
    ("住宿", ("住宿", "酒店", "宾馆", "民宿", "别墅")),
    ("门票", ("门票", "入场券", "景区")),
    ("医疗", ("医院", "门诊", "看病", "药", "医疗")),
    ("保健品", ("鱼油", "钙片", "保健品", "健胃消食片")),
    ("学习", ("学习", "课程", "书", "打印", "论文", "培训", "讲义", "刷题")),
    ("考试报名", ("报考", "考试费", "报名费")),
    ("文具耗材", ("宣纸", "墨汁", "笔尖", "文具")),
    ("服饰", ("衣服", "背心", "鞋", "表带")),
    ("个护用品", ("卫生巾", "耳塞", "眼罩", "洗漱用品")),
    ("日用品", ("纸巾", "保温杯", "雨伞", "电热水袋")),
    ("数码配件", ("充电器", "apple pencil", "手机壳", "数据线", "手机支架")),
    ("潮玩", ("popmart", "泡泡玛特")),
    ("网络服务", ("梯子", "代理服务", "vpn")),
    ("AI工具", ("deepseek", "chatgpt", "chat-gpt", "openai")),
    ("账单还款", ("白条账单", "信用卡还款", "账单还款")),
    ("唱歌娱乐", ("ktv", "唱k", "唱歌")),
    ("社交活动", ("团建",)),
    ("人情往来", ("红包", "礼物", "礼金", "人情", "请客")),
    ("数码设备", ("手机", "电脑", "平板", "耳机", "相机", "数码")),
)

MEAL_RULES = (
    ("早餐", ("早餐", "早饭")),
    ("午餐", ("午餐", "午饭")),
    ("晚餐", ("晚餐", "晚饭")),
    ("夜宵", ("夜宵", "宵夜")),
)

SCENE_REQUIRED_CATEGORIES = {
    "地铁": "交通", "公交": "交通", "打车": "交通",
    "铁路出行": "交通", "航空出行": "交通", "租车": "交通",
    "加油": "交通", "停车": "交通",
    "共享单车": "交通", "交通罚款": "交通",
    "房租": "居住", "水电燃气": "居住", "物业": "居住",
    "宽带": "居住", "住宿": "居住",
}

PROJECT_RULES = (
    ("北京生活", ("北京",)),
    ("入职准备", ("入职", "面试", "工牌", "体检")),
)

INCOME_SCENE_RULES = (
    ("餐补", ("餐补", "餐费补贴", "伙食补贴")),
    ("交通补贴", ("交通补贴", "通勤补贴")),
    ("住房补贴", ("住房补贴", "租房补贴")),
    ("工资", ("工资", "薪资", "薪水")),
    ("奖金", ("奖金", "年终奖", "绩效奖")),
    ("奖学金", ("奖学金",)),
    ("报销", ("报销",)),
    ("退款", ("退款", "退回")),
    ("理财收益", ("理财", "利息", "收益")),
    ("生日红包", ("生日红包",)),
    ("红包", ("红包",)),
    ("兼职", ("兼职", "稿费", "劳务费")),
    ("二手转卖", ("二手", "卖废品", "出售闲置", "卖出闲置")),
    ("退税", ("退税",)),
    ("助学金", ("助学金",)),
    ("家庭支持", ("爸爸转账", "妈妈转账", "爸爸转", "妈妈转", "生活费")),
    ("棋牌收入", ("麻将赚", "打麻将赚", "德州扑克赢")),
)

CATEGORY_DEFAULT_TAGS = {
    ("支出", "餐饮"): "日常餐饮",
    ("支出", "交通"): "日常出行",
    ("支出", "购物"): "日常购物",
    ("支出", "娱乐"): "休闲娱乐",
    ("支出", "居住"): "居家生活",
    ("支出", "医疗"): "医疗健康",
    ("支出", "教育"): "学习成长",
    ("支出", "人情"): "人情往来",
    ("支出", "其他"): "其他支出",
    ("收入", "工资"): "工资收入",
    ("收入", "奖金"): "奖金收入",
    ("收入", "兼职"): "兼职收入",
    ("收入", "理财"): "理财收益",
    ("收入", "退款"): "退款收入",
    ("收入", "退税"): "退税收入",
    ("收入", "报销"): "报销收入",
    ("收入", "补贴"): "补贴收入",
    ("收入", "红包"): "红包收入",
    ("收入", "人情"): "人情收入",
    ("收入", "其他"): "其他收入",
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
    result = []
    for tag in [*clean_tags(existing_tags), *clean_tags(generated_tags)]:
        if tag not in result:
            result.append(tag)
        if len(result) >= limit:
            break
    return result


def generate_tags(
    transaction,
    raw_text=None,
    preserve_existing=True,
    preserve_trusted=True,
):
    """Generate up to three evidence-backed scene/source/project tags."""
    transaction = dict(transaction or {})
    existing = clean_tags(transaction.get("tags"))
    description = str(transaction.get("description") or "")
    text = f"{description} {raw_text or ''}".lower()
    category = str(transaction.get("category") or "其他")
    txn_type = str(transaction.get("type") or "支出")
    txn_date = _as_date(transaction.get("date"))

    retained = list(existing) if preserve_existing else []
    if not preserve_existing and preserve_trusted:
        retained = [
            tag for tag in existing
            if _trusted_existing_tag_is_supported(tag, txn_date, text)
        ]

    generated = []
    if text_mentions_personal_advance(description, raw_text):
        generated.append(PERSONAL_ADVANCE_TAG)
    if is_meal_subsidy_expense(
        {
            "type": txn_type,
            "description": description,
            "tags": raw_text,
        }
    ):
        generated.append(MEAL_SUBSIDY_USAGE_TAG)

    if txn_type == "收入":
        income_scene = _first_match(text, INCOME_SCENE_RULES)
        if income_scene:
            generated.append(income_scene)
        else:
            generated.append(category)
        if category == "补贴" and _contains_any(text, ("公司", "单位", "雇主")):
            generated.append("公司福利")
    else:
        scene = _first_scene_match(text, category)
        if scene:
            generated.append(scene)

        if category == "餐饮":
            meal = _meal_tag(text, scene)
            if meal:
                generated.append(meal)

        if _contains_any(text, ("海南", "三亚", "陵水", "万宁", "海口", "分界洲", "西岛")):
            generated.append("2026海南旅游")
        elif _contains_any(text, ("旅游", "旅行", "度假")):
            generated.append("旅游")

        project = _first_match(text, PROJECT_RULES)
        if project:
            generated.append(project)

        if category == "交通" and _contains_any(text, ("上班", "下班", "通勤")):
            generated.append("通勤")

    category_context = CATEGORY_DEFAULT_TAGS.get(
        (txn_type, category),
        "其他收入" if txn_type == "收入" else "其他支出",
    )
    generated.append(category_context)
    if txn_type == "支出":
        generated.append("刚需" if bool(transaction.get("is_need")) else "非刚需")
        generated.append("固定支出" if bool(transaction.get("is_fixed")) else "变动支出")
    else:
        generated.append(category)
        generated.append("收入记录")
        generated.append("场景待细分")
    tags = merge_tags(retained, generated)
    return ",".join(tags[:MAX_TAGS])


def backfill_tags(apply=False, limit_examples=20):
    """Fill empty tags on active transactions without touching deleted rows."""
    return _retag_transactions(
        apply=apply,
        only_empty=True,
        limit_examples=limit_examples,
    )


def retag_all_transactions(apply=False, limit_examples=20):
    """Rebuild all active tags from existing ledger facts, never from AI guesses."""
    return _retag_transactions(
        apply=apply,
        only_empty=False,
        limit_examples=limit_examples,
    )


def _retag_transactions(apply, only_empty, limit_examples):
    from .derived_fields import DERIVED_COLUMNS, derived_values
    from . import ledger

    derived_update_sql = ", ".join(
        f"{column} = ?" for column in DERIVED_COLUMNS
    )
    ledger.init_db()
    where = "AND TRIM(COALESCE(tags, '')) = ''" if only_empty else ""
    with ledger.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT rowid, id, transaction_uid, date, type, category, amount,
                   description, tags, is_need, is_fixed
            FROM transactions
            WHERE status = 'active' {where}
            ORDER BY rowid
            """
        ).fetchall()

        planned = []
        old_tag_total = 0
        new_tag_total = 0
        old_distinct = set()
        new_distinct = set()
        for row in rows:
            old_tags = clean_tags(row[8])
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
                },
                preserve_existing=False,
                preserve_trusted=True,
            )
            new_tags = clean_tags(tags)
            old_tag_total += len(old_tags)
            new_tag_total += len(new_tags)
            old_distinct.update(old_tags)
            new_distinct.update(new_tags)
            if tags == ",".join(old_tags):
                continue
            planned.append(
                {
                    "rowid": int(row[0]),
                    "local_id": int(row[1] or row[0]),
                    "transaction_uid": str(row[2] or ""),
                    "date": str(row[3] or ""),
                    "type": str(row[4] or "支出"),
                    "category": str(row[5] or "其他"),
                    "amount": float(row[6] or 0),
                    "description": str(row[7] or ""),
                    "old_tags": ",".join(old_tags),
                    "tags": tags,
                    "is_need": int(bool(row[9])),
                    "is_fixed": int(bool(row[10])),
                    "status": "active",
                }
            )

        if apply:
            for item in planned:
                conn.execute(
                    f"""
                    UPDATE transactions
                    SET tags = ?, sync_status = 'pending', sync_error = '',
                        updated_at = CURRENT_TIMESTAMP,
                        {derived_update_sql}
                    WHERE rowid = ? AND status = 'active'
                    """,
                    (
                        item["tags"],
                        *derived_values(item),
                        item["rowid"],
                    ),
                )
                if item["transaction_uid"]:
                    conn.execute(
                        """
                        INSERT INTO sync_outbox
                            (transaction_uid, operation, status, retry_count, updated_at)
                        SELECT ?, 'update', 'pending', 0, CURRENT_TIMESTAMP
                        WHERE NOT EXISTS (
                            SELECT 1 FROM sync_outbox
                            WHERE transaction_uid = ?
                              AND status IN ('pending', 'processing', 'in_progress')
                        )
                        """,
                        (item["transaction_uid"], item["transaction_uid"]),
                    )

    inspected = len(rows)
    return {
        "success": True,
        "mode": "apply" if apply else "dry-run",
        "scope": "empty" if only_empty else "all_active",
        "inspected_count": inspected,
        "planned_count": len(planned),
        "updated_count": len(planned) if apply else 0,
        "old_distinct_tags": len(old_distinct),
        "new_distinct_tags": len(new_distinct),
        "old_average_tags": round(old_tag_total / inspected, 2) if inspected else 0.0,
        "new_average_tags": round(new_tag_total / inspected, 2) if inspected else 0.0,
        "examples": [
            {
                "local_id": item["local_id"],
                "date": item["date"],
                "type": item["type"],
                "category": item["category"],
                "old_tags": item["old_tags"],
                "tags": item["tags"],
            }
            for item in planned[: max(0, int(limit_examples))]
        ],
    }


def _trusted_existing_tag_is_supported(tag, txn_date, text):
    if tag == PERSONAL_ADVANCE_TAG:
        return True
    if tag == "2026海南旅游":
        return bool(
            _contains_any(text, ("海南", "三亚", "陵水", "万宁", "海口", "分界洲", "西岛"))
            or (
                txn_date
                and datetime.date(2026, 5, 30)
                <= txn_date
                <= datetime.date(2026, 6, 5)
            )
        )
    return False


def _contains_any(text, keywords):
    return any(str(keyword).lower() in text for keyword in keywords)


def _first_match(text, rules):
    for tag, keywords in rules:
        if _contains_any(text, keywords):
            return tag
    return None


def _first_scene_match(text, category):
    for tag, keywords in SCENE_RULES:
        required = SCENE_REQUIRED_CATEGORIES.get(tag)
        if required and category != required:
            continue
        if _contains_any(text, keywords):
            return tag
    return None


def _meal_tag(text, scene):
    explicit = _first_match(text, MEAL_RULES)
    if explicit:
        return explicit
    non_meal_scenes = {
        "水果", "零食小吃", "咖啡", "奶茶", "乳制品", "酒饮", "饮品", "烘焙面点"
    }
    if scene in non_meal_scenes:
        return None
    is_meal = scene in {"食堂", "外卖", "聚餐", "快餐", "正餐"} or _contains_any(
        text, ("吃", "饭", "餐")
    )
    if not is_meal:
        return None
    if "早上" in text:
        return "早餐"
    if "中午" in text:
        return "午餐"
    if "晚上" in text:
        return "晚餐"
    return None


def _as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def main():
    parser = argparse.ArgumentParser(description="Generate and repair transaction tags.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--backfill-tags", action="store_true")
    action.add_argument("--retag-all", action="store_true")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    operation = retag_all_transactions if args.retag_all else backfill_tags
    print(
        json.dumps(
            operation(apply=args.apply),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

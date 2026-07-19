MEAL_SUBSIDY_USAGE_TAG = "餐补消费"
MEAL_SUBSIDY_USAGE_KEYWORDS = (
    "餐补",
    "餐卡",
    "饭卡",
    "餐券",
    "伙食卡",
)


def text_mentions_meal_subsidy_usage(*values):
    text = " ".join(str(value or "") for value in values)
    return any(keyword in text for keyword in MEAL_SUBSIDY_USAGE_KEYWORDS)


def is_meal_subsidy_expense(transaction):
    transaction = dict(transaction or {})
    if str(transaction.get("type") or "") != "支出":
        return False
    return text_mentions_meal_subsidy_usage(
        transaction.get("description"),
        transaction.get("tags"),
    )

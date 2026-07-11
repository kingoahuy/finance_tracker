import json
import logging
import os
import time

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    from .config import PROJECT_ROOT, load_env_file
except ImportError:
    from config import PROJECT_ROOT, load_env_file


LOGGER = logging.getLogger("finance_tracker.deepseek_reports")

COMMON_PROMPT_RULES = """
统一约束：
1. 你是个人财务分析助手。
2. 只能使用 data_payload 中的数据。
3. 不得编造金额、日期、分类、标签、笔数。
4. 金额保留 2 位小数。
5. 不输出 JSON，只输出 Markdown。
6. 不输出完整隐私流水。
7. 数据为空时明确说明暂无数据。
8. 语气简洁、实用、适合飞书聊天窗口阅读。
9. data_payload 中普通收入、支出、预算、分类、标签和趋势统计均不含“个人垫付”；如存在 personal_advance，必须单独说明，不得并入普通收支。
""".strip()

MONTHLY_BILL_PROMPT = (
    COMMON_PROMPT_RULES
    + "\n\n请输出本月账单，覆盖：核心收支、支出结构、预算进度、"
    "主要消费场景、大额支出、一句话判断、建议。"
)

DAILY_REPORT_PROMPT = (
    COMMON_PROMPT_RULES
    + "\n\n请输出记账日报，覆盖：今日概览、今日分类支出、今日消费标签、"
    "今日明细、预算进度、今日小结、明日建议。"
)

MONTHLY_TAG_ANALYSIS_PROMPT = (
    COMMON_PROMPT_RULES
    + "\n\n请输出本月标签分析，覆盖：标签覆盖情况、Top 消费标签、"
    "消费场景解读、高频小额标签、刚需与非刚需、较上月变化、建议。"
)

MONTHLY_CONSUMPTION_REPORT_PROMPT = (
    COMMON_PROMPT_RULES
    + "\n\n请输出本月消费报告，覆盖：总体判断、核心数据、消费结构、"
    "标签场景、刚需与非刚需、固定与变动支出、异常与大额支出、"
    "较上月变化、下月建议、一句话总结。"
)

PROMPTS = {
    "monthly_bill": MONTHLY_BILL_PROMPT,
    "daily_report": DAILY_REPORT_PROMPT,
    "monthly_tag_analysis": MONTHLY_TAG_ANALYSIS_PROMPT,
    "monthly_consumption_report": MONTHLY_CONSUMPTION_REPORT_PROMPT,
}

FALLBACKS = {
    "monthly_bill": lambda payload: fallback_monthly_bill_markdown(payload),
    "daily_report": lambda payload: fallback_daily_report_markdown(payload),
    "monthly_tag_analysis": lambda payload: fallback_monthly_tag_analysis_markdown(payload),
    "monthly_consumption_report": lambda payload: fallback_monthly_consumption_report_markdown(payload),
}


def call_deepseek_report(prompt_name: str, data_payload: dict) -> str:
    prompt_name = str(prompt_name or "").strip()
    payload = dict(data_payload or {})
    if prompt_name not in PROMPTS:
        raise ValueError(f"Unsupported report prompt: {prompt_name}")

    started = time.monotonic()
    period = payload.get("month") or payload.get("date") or "unknown"
    try:
        config = _get_deepseek_config()
        if not config["api_key"] or OpenAI is None:
            raise RuntimeError("DeepSeek is not configured.")
        client = OpenAI(
            api_key=config["api_key"],
            base_url=config["base_url"],
            # Menu callbacks run on the Feishu event connection. Automatic
            # SDK retries can stretch a 15-second timeout to nearly a minute,
            # causing Feishu to redeliver the same menu event.
            max_retries=0,
        )
        response = client.chat.completions.create(
            model=config["model"],
            timeout=config["timeout"],
            max_tokens=config["max_tokens"],
            messages=[
                {"role": "system", "content": PROMPTS[prompt_name]},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"data_payload": payload},
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ],
        )
        markdown = str(response.choices[0].message.content or "").strip()
        if not markdown:
            raise RuntimeError("DeepSeek returned empty content.")
        LOGGER.info(
            "DeepSeek report prompt=%s period=%s success=%s elapsed_ms=%d",
            prompt_name,
            period,
            True,
            _elapsed_ms(started),
        )
        return markdown
    except Exception as exc:
        LOGGER.warning(
            "DeepSeek report prompt=%s period=%s success=%s elapsed_ms=%d error_type=%s",
            prompt_name,
            period,
            False,
            _elapsed_ms(started),
            type(exc).__name__,
        )
        return _fallback(prompt_name, payload)


def fallback_monthly_bill_markdown(data_payload):
    payload = data_payload or {}
    overview = payload.get("overview") or {}
    budget = payload.get("budget") or {}
    return "\n".join(
        [
            f"# 本月账单 {payload.get('month') or '暂无数据'}",
            "",
            "## 核心收支",
            f"- 收入：{_money_text(overview.get('income'))}",
            f"- 支出：{_money_text(overview.get('expense'))}",
            f"- 结余：{_money_text(overview.get('balance'))}",
            f"- 交易笔数：{_value_text(overview.get('transaction_count'))}",
            "",
            "## 个人垫付（单独列示）",
            *_personal_advance_lines(payload.get("personal_advance")),
            "",
            "## 支出结构",
            *_summary_lines(payload.get("top_categories"), "category"),
            "",
            "## 预算进度",
            f"- 月预算：{_money_text(budget.get('monthly_budget'))}",
            f"- 已使用：{_money_text(budget.get('used'))}",
            f"- 使用率：{_percent_text(budget.get('usage_rate'))}",
            f"- 状态：{_value_text(budget.get('status'))}",
            "",
            "## 主要消费场景",
            *_summary_lines(payload.get("top_tags"), "tag"),
            "",
            "## 大额支出",
            *_transaction_lines(payload.get("top_expenses")),
            "",
            "## 一句话判断",
            _monthly_judgement(overview, budget),
            "",
            "## 建议",
            "- 以上内容由本地数据生成，DeepSeek 暂不可用；建议优先复盘占比最高的分类和标签。",
        ]
    ).strip()


def fallback_daily_report_markdown(data_payload):
    payload = data_payload or {}
    overview = payload.get("overview") or {}
    budget = payload.get("budget") or {}
    return "\n".join(
        [
            f"# 记账日报 {payload.get('date') or '暂无数据'}",
            "",
            "## 今日概览",
            f"- 收入：{_money_text(overview.get('income'))}",
            f"- 支出：{_money_text(overview.get('expense'))}",
            f"- 结余：{_money_text(overview.get('balance'))}",
            f"- 交易笔数：{_value_text(overview.get('transaction_count'))}",
            "",
            "## 个人垫付（单独列示）",
            *_personal_advance_lines(payload.get("personal_advance")),
            "",
            "## 今日分类支出",
            *_summary_lines(payload.get("category_summary"), "category"),
            "",
            "## 今日消费标签",
            *_summary_lines(payload.get("tag_summary"), "tag"),
            "",
            "## 今日明细",
            *_transaction_lines(payload.get("transactions")),
            "",
            "## 预算进度",
            f"- 已使用：{_money_text(budget.get('used'))}",
            f"- 使用率：{_percent_text(budget.get('usage_rate'))}",
            f"- 状态：{_value_text(budget.get('status'))}",
            "",
            "## 今日小结",
            "- DeepSeek 暂不可用，以上为本地 fallback 报表。",
            "",
            "## 明日建议",
            "- 继续记录真实流水，优先关注高频小额和预算接近超支的部分。",
        ]
    ).strip()


def fallback_monthly_tag_analysis_markdown(data_payload):
    payload = data_payload or {}
    overview = payload.get("overview") or {}
    groups = payload.get("tag_groups") or {}
    return "\n".join(
        [
            f"# 本月标签分析 {payload.get('month') or '暂无数据'}",
            "",
            "## 标签覆盖情况",
            f"- 支出笔数：{_value_text(overview.get('transaction_count'))}",
            f"- 已打标签：{_value_text(overview.get('tagged_transaction_count'))}",
            f"- 未打标签：{_value_text(overview.get('untagged_transaction_count'))}",
            f"- 覆盖率：{_percent_text(overview.get('tag_coverage_rate'))}",
            "",
            "## 个人垫付（单独列示）",
            *_personal_advance_lines(payload.get("personal_advance")),
            "",
            "## Top 消费标签",
            *_summary_lines(payload.get("tag_summary"), "tag"),
            "",
            "## 消费场景解读",
            *_group_lines(groups),
            "",
            "## 较上月变化",
            *_compare_lines(payload.get("compare_previous_month")),
            "",
            "## 建议",
            "- DeepSeek 暂不可用；建议先补齐未打标签流水，再复盘非刚需和小额高频标签。",
        ]
    ).strip()


def fallback_monthly_consumption_report_markdown(data_payload):
    payload = data_payload or {}
    overview = payload.get("overview") or {}
    budget = payload.get("budget") or {}
    return "\n".join(
        [
            f"# 本月消费报告 {payload.get('month') or '暂无数据'}",
            "",
            "## 总体判断",
            _monthly_judgement(overview, budget),
            "",
            "## 核心数据",
            f"- 收入：{_money_text(overview.get('income'))}",
            f"- 支出：{_money_text(overview.get('expense'))}",
            f"- 结余：{_money_text(overview.get('balance'))}",
            f"- 日均支出：{_money_text(overview.get('daily_avg_expense'))}",
            "",
            "## 个人垫付（单独列示）",
            *_personal_advance_lines(payload.get("personal_advance")),
            "",
            "## 消费结构",
            *_summary_lines(payload.get("category_summary"), "category"),
            "",
            "## 标签场景",
            *_summary_lines(payload.get("tag_summary"), "tag"),
            "",
            "## 刚需与非刚需",
            *_typed_summary_lines(payload.get("need_vs_want")),
            "",
            "## 固定与变动支出",
            *_typed_summary_lines(payload.get("fixed_vs_variable")),
            "",
            "## 异常与大额支出",
            *_transaction_lines(payload.get("top_expenses")),
            "",
            "## 较上月变化",
            *_compare_lines(payload.get("compare_previous_month")),
            "",
            "## 下月建议",
            "- DeepSeek 暂不可用；建议围绕最高分类、最高标签和大额支出设置下月预算。",
            "",
            "## 一句话总结",
            "- 本报表由本地 fallback 模板生成，所有事实来自 data_payload。",
        ]
    ).strip()


def _get_deepseek_config():
    load_env_file(PROJECT_ROOT / ".env", override=False)
    return {
        "api_key": os.getenv("DEEPSEEK_API_KEY", "").strip(),
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip(),
        "timeout": max(1, min(int(os.getenv("AI_PARSER_TIMEOUT_SECONDS", "15") or "15"), 60)),
        "max_tokens": max(
            300,
            min(
                int(os.getenv("DEEPSEEK_REPORT_MAX_TOKENS", "1200") or "1200"),
                8000,
            ),
        ),
    }


def _fallback(prompt_name, payload):
    return FALLBACKS[prompt_name](payload)


def _personal_advance_lines(advance):
    advance = advance or {}
    expense = float(advance.get("advance_expense") or 0)
    reimbursement = float(advance.get("advance_reimbursement") or 0)
    period_balance = float(advance.get("advance_balance") or 0)
    current_balance = float(advance.get("current_balance", period_balance) or 0)
    count = int(advance.get("transaction_count") or 0)
    if not count and not expense and not reimbursement and not current_balance:
        return ["- 暂无个人垫付记录。"]
    return [
        "- 以下金额不计入普通收入、支出、预算和消费标签统计。",
        f"- 本期垫付支出：{_money_text(expense)}",
        f"- 本期垫付回款：{_money_text(reimbursement)}",
        f"- 本期垫付净额：{_money_text(period_balance)}",
        f"- 当前垫付余额：{_money_text(current_balance)}",
        f"- 本期垫付笔数：{_value_text(count)}",
    ]


def _summary_lines(rows, name_key):
    rows = list(rows or [])
    if not rows:
        return ["- 暂无数据"]
    return [
        f"- {row.get(name_key) or '暂无数据'}：{_money_text(row.get('amount'))}，{_value_text(row.get('count'))} 笔"
        for row in rows[:10]
    ]


def _typed_summary_lines(rows):
    rows = list(rows or [])
    if not rows:
        return ["- 暂无数据"]
    return [
        f"- {row.get('type') or '暂无数据'}：{_money_text(row.get('amount'))}，占比 {_percent_text(row.get('share'))}"
        for row in rows[:10]
    ]


def _transaction_lines(rows):
    rows = list(rows or [])
    if not rows:
        return ["- 暂无数据"]
    return [
        f"- {row.get('date') or '暂无数据'} {row.get('category') or '暂无数据'} {_money_text(row.get('amount'))} {row.get('description') or ''}".strip()
        for row in rows[:10]
    ]


def _group_lines(groups):
    if not groups:
        return ["- 暂无数据"]
    lines = []
    for name in ["刚需", "非刚需", "固定支出", "小额高频"]:
        item = groups.get(name) or {}
        lines.append(
            f"- {name}：{_money_text(item.get('amount'))}，{_value_text(item.get('count'))} 笔"
        )
    return lines


def _compare_lines(compare):
    compare = compare or {}
    if not compare:
        return ["- 暂无数据"]
    lines = [f"- 对比月份：{compare.get('previous_month') or '暂无数据'}"]
    for key, label in [("income", "收入"), ("expense", "支出"), ("balance", "结余")]:
        item = compare.get(key) or {}
        lines.append(
            f"- {label}变化：{_money_text(item.get('change'))}，变化率 {_percent_text(item.get('change_rate'))}"
        )
    return lines


def _monthly_judgement(overview, budget):
    expense = overview.get("expense")
    usage_rate = budget.get("usage_rate")
    if expense in (None, ""):
        return "- 暂无数据"
    return f"- 本期支出 {_money_text(expense)}，预算使用率 {_percent_text(usage_rate)}，状态：{_value_text(budget.get('status'))}。"


def _money_text(value):
    if value in (None, ""):
        return "暂无数据"
    return f"¥{float(value):.2f}"


def _percent_text(value):
    if value in (None, ""):
        return "暂无数据"
    return f"{float(value):.2f}%"


def _value_text(value):
    if value in (None, ""):
        return "暂无数据"
    return str(value)


def _elapsed_ms(started):
    return int((time.monotonic() - started) * 1000)

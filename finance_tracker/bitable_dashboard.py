import argparse
import datetime
import json
from collections import Counter, defaultdict
from pathlib import Path

try:
    import lark_oapi as lark
    from lark_oapi.api.bitable.v1 import (
        AppTable,
        AppTableField,
        AppTableRecord,
        BatchCreateAppTableRecordRequest,
        BatchCreateAppTableRecordRequestBody,
        BatchDeleteAppTableRecordRequest,
        BatchDeleteAppTableRecordRequestBody,
        BatchUpdateAppTableRecordRequest,
        BatchUpdateAppTableRecordRequestBody,
        CreateAppTableFieldRequest,
        CreateAppTableRequest,
        CreateAppTableRequestBody,
        ListAppTableFieldRequest,
        ListAppTableRecordRequest,
        ListAppTableRequest,
    )
except ImportError:
    lark = None

try:
    from . import analytics
    from .feishu_client import response_result
    from .feishu_config import get_feishu_config
except ImportError:
    import analytics
    from feishu_client import response_result
    from feishu_config import get_feishu_config

try:
    from .config import PROJECT_ROOT
except ImportError:
    from config import PROJECT_ROOT


TEXT = 1
NUMBER = 2
MULTI_SELECT = 4
DATE = 5


TABLE_DEFINITIONS = {
    "财务总览表": {
        "key_fields": ("统计周期",),
        "fields": {
            "统计周期": TEXT,
            "总收入": NUMBER,
            "总支出": NUMBER,
            "净收入": NUMBER,
            "结余率": NUMBER,
            "交易笔数": NUMBER,
            "日均支出": NUMBER,
            "最大支出分类": TEXT,
            "更新时间": DATE,
        },
    },
    "月度汇总表": {
        "key_fields": ("年月",),
        "fields": {
            "年月": TEXT,
            "收入": NUMBER,
            "支出": NUMBER,
            "净收入": NUMBER,
            "结余率": NUMBER,
            "支出笔数": NUMBER,
            "收入笔数": NUMBER,
        },
    },
    "分类月度汇总表": {
        "key_fields": ("年月", "分类"),
        "fields": {
            "年月": TEXT,
            "分类": TEXT,
            "金额": NUMBER,
            "占比": NUMBER,
            "笔数": NUMBER,
            "日均金额": NUMBER,
        },
    },
    "每日支出趋势表": {
        "key_fields": ("日期",),
        "fields": {
            "日期": DATE,
            "年月": TEXT,
            "支出金额": NUMBER,
            "支出笔数": NUMBER,
            "7日移动平均": NUMBER,
        },
    },
    "收入来源汇总表": {
        "key_fields": ("年月", "收入分类"),
        "fields": {
            "年月": TEXT,
            "收入分类": TEXT,
            "金额": NUMBER,
            "占比": NUMBER,
            "笔数": NUMBER,
        },
    },
    "标签分析表": {
        "key_fields": ("年月", "标签"),
        "fields": {
            "年月": TEXT,
            "标签": TEXT,
            "金额": NUMBER,
            "笔数": NUMBER,
            "关联分类": MULTI_SELECT,
        },
    },
    "刚需/非刚需分析表": {
        "key_fields": ("年月", "类型"),
        "fields": {
            "年月": TEXT,
            "类型": TEXT,
            "金额": NUMBER,
            "占比": NUMBER,
        },
    },
    "固定/变动支出分析表": {
        "key_fields": ("年月", "类型"),
        "fields": {
            "年月": TEXT,
            "类型": TEXT,
            "金额": NUMBER,
            "占比": NUMBER,
        },
    },
    "大额支出表": {
        "key_fields": ("年月", "日期", "分类", "金额", "描述", "标签"),
        "fields": {
            "年月": TEXT,
            "日期": DATE,
            "分类": TEXT,
            "金额": NUMBER,
            "描述": TEXT,
            "标签": MULTI_SELECT,
        },
    },
    "预算预警表": {
        "key_fields": ("年月", "分类"),
        "fields": {
            "年月": TEXT,
            "分类": TEXT,
            "预算": NUMBER,
            "已用": NUMBER,
            "剩余": NUMBER,
            "使用率": NUMBER,
            "状态": TEXT,
        },
    },
    "财务洞察表": {
        "key_fields": ("年月",),
        "fields": {
            "年月": TEXT,
            "一句话总结": TEXT,
            "主要支出分类": TEXT,
            "异常支出": TEXT,
            "节省建议": TEXT,
            "下月提醒": TEXT,
            "生成时间": DATE,
        },
    },
}


DASHBOARD_GUIDE = """# 飞书财务分析看板搭建指南

## 1. 使用范围

本指南用于在飞书多维表格中，基于本地脚本已经同步完成的汇总表，手动搭建财务分析看板。

- SQLite 是主数据源。
- `finance_tracker.analytics` 负责本地计算。
- `finance_tracker.bitable_dashboard` 负责同步汇总表。
- 飞书仪表盘、图表、筛选器和布局需要在飞书网页或客户端中手动创建。
- 搭建看板不会改变本地账本，也不需要修改原始明细表。

开始前建议运行：

```powershell
python -m finance_tracker.bitable_dashboard --check
python -m finance_tracker.bitable_dashboard --sync-summary
python -m finance_tracker.bitable_dashboard --audit
```

## 2. 新建仪表盘

1. 打开当前财务多维表格应用。
2. 在左侧导航中点击“新建”，选择“仪表盘”。
3. 命名为“个人财务分析看板”。
4. 新建一个全局筛选器“年月”，默认选择当前月份。
5. 各组件按下文指定的数据表、筛选条件和排序方式创建。

> 汇总表和字段可由 `--check` 自动创建，但仪表盘、图表、指标卡、筛选器、颜色和布局必须手动在飞书端创建。

## 3. 总览区

建议放在看板第一行，使用五张指标卡。

| 组件 | 数据来源表 | 图表类型 | 筛选条件 | 指标与排序 |
| --- | --- | --- | --- | --- |
| 本月收入 | 月度汇总表 | 指标卡 | 年月等于全局筛选月份 | 指标选择“收入”；无需排序 |
| 本月支出 | 月度汇总表 | 指标卡 | 年月等于全局筛选月份 | 指标选择“支出”；无需排序 |
| 本月结余 | 月度汇总表 | 指标卡 | 年月等于全局筛选月份 | 指标选择“净收入”；无需排序 |
| 结余率 | 月度汇总表 | 指标卡 | 年月等于全局筛选月份 | 指标选择“结余率”；显示为百分比 |
| 今日支出 | 财务总览表 | 指标卡 | 统计周期等于“全部有效流水” | 当前同步表没有独立“今日支出”字段时，可先用每日支出趋势表筛选日期为今天并汇总“支出金额” |

推荐颜色：

- 收入：绿色。
- 支出：红色或橙色。
- 结余：蓝色。
- 结余率：大于等于 20% 使用绿色，低于 0 使用红色。
- 今日支出：中性色，超过日均预算时改为橙色。

## 4. 趋势区

| 组件 | 数据来源表 | 图表类型 | 筛选条件 | 维度、指标与排序 |
| --- | --- | --- | --- | --- |
| 月度收入/支出/净收入趋势 | 月度汇总表 | 多系列折线图 | 可选择最近 6 或 12 个月 | X 轴“年月”；Y 轴“收入、支出、净收入”；年月升序 |
| 每日支出趋势 | 每日支出趋势表 | 折线图 | 年月等于全局筛选月份 | X 轴“日期”；Y 轴“支出金额”；日期升序 |
| 7日移动平均 | 每日支出趋势表 | 折线图，建议与每日支出放在同一图表 | 年月等于全局筛选月份 | X 轴“日期”；第二条 Y 轴序列“7日移动平均”；日期升序 |

显示建议：

- 每日支出使用较浅颜色，7 日移动平均使用深色粗线。
- 月度趋势图开启数据点提示，但不要同时显示所有数据标签，避免手机端拥挤。
- 如果飞书版本不支持双序列，可分别创建“每日支出”和“7 日均线”两张折线图。

## 5. 分类区

| 组件 | 数据来源表 | 图表类型 | 筛选条件 | 维度、指标与排序 |
| --- | --- | --- | --- | --- |
| 分类支出 | 分类月度汇总表 | 横向柱状图 | 年月等于全局筛选月份 | 分类为维度，金额为指标；金额降序 |
| 分类支出占比 | 分类月度汇总表 | 环形图 | 年月等于全局筛选月份，金额大于 0 | 分类为维度，占比或金额为指标；金额降序 |
| 标签消费分析 | 标签分析表 | 横向柱状图或词云 | 年月等于全局筛选月份 | 标签为维度，金额为指标；金额降序；建议只显示前 10 |

分类图建议：

- 环形图最多显示前 6 类，其余归为“其他”。
- 标签分析优先查看“旅游、外卖、咖啡、通勤、住宿、订阅”等场景标签。
- 点击标签组件时，可联动筛选关联分类。

## 6. 预算区

| 组件 | 数据来源表 | 图表类型 | 筛选条件 | 维度、指标与排序 |
| --- | --- | --- | --- | --- |
| 预算使用率 | 预算预警表 | 仪表盘或进度条 | 年月等于全局筛选月份 | 分类为维度，使用率为指标；使用率降序 |
| 接近超支分类 | 预算预警表 | 表格或柱状图 | 年月等于筛选月份，状态等于“接近超支” | 显示分类、预算、已用、剩余、使用率；使用率降序 |
| 已超支分类 | 预算预警表 | 表格或柱状图 | 年月等于筛选月份，状态等于“已超支” | 显示分类、预算、已用、剩余、使用率；使用率降序 |

条件格式建议：

- 正常：绿色。
- 接近超支：橙色。
- 已超支：红色。
- “剩余”为负数时显示红色。

## 7. 结构区

| 组件 | 数据来源表 | 图表类型 | 筛选条件 | 维度、指标与排序 |
| --- | --- | --- | --- | --- |
| 刚需 vs 非刚需 | 刚需/非刚需分析表 | 环形图 | 年月等于全局筛选月份 | 类型为维度，金额为指标；金额降序 |
| 固定支出 vs 变动支出 | 固定/变动支出分析表 | 环形图或堆叠柱状图 | 年月等于全局筛选月份 | 类型为维度，金额为指标；金额降序 |

建议在图表旁增加百分比数据标签。刚需和固定支出使用较稳重的颜色，非刚需和变动支出使用橙色。

## 8. 明细区

| 组件 | 数据来源表 | 图表类型 | 筛选条件 | 字段与排序 |
| --- | --- | --- | --- | --- |
| Top 10 大额支出 | 大额支出表 | 表格 | 年月等于全局筛选月份 | 显示日期、分类、金额、描述、标签；金额降序；限制 10 条 |
| 最近流水 | 原始明细表 | 表格 | 状态等于 active；可叠加年月、分类、标签筛选 | 显示日期、类型、分类、金额、描述、标签；日期降序，再按创建时间降序 |

隐私建议：

- 手机端默认隐藏飞书消息 ID、本地 ID、交易 UID、删除人等审计字段。
- 最近流水表默认只展示最近 20 条。
- 如果需要共享看板，先确认描述字段中没有不适合共享的个人信息。

## 9. 洞察区

| 组件 | 数据来源表 | 图表类型 | 筛选条件 | 字段与排序 |
| --- | --- | --- | --- | --- |
| 本月一句话总结 | 财务洞察表 | 文本卡或表格 | 年月等于全局筛选月份 | 显示“一句话总结”；生成时间降序 |
| 异常支出 | 财务洞察表 | 文本卡 | 年月等于全局筛选月份 | 显示“异常支出”；生成时间降序 |
| 节省建议 | 财务洞察表 | 文本卡 | 年月等于全局筛选月份 | 显示“节省建议”；生成时间降序 |
| 下月提醒 | 财务洞察表 | 文本卡 | 年月等于全局筛选月份 | 显示“下月提醒”；生成时间降序 |

“异常支出”当前是结构化 JSON 文本。飞书文本卡可直接展示；若希望更美观，可在后续阶段增加单独的异常支出明细表。

## 10. 手机端布局

建议采用单列优先、两列为辅：

1. 第一屏：本月收入、本月支出、本月结余、结余率、今日支出。
2. 第二屏：每日支出与 7 日移动平均。
3. 第三屏：分类柱状图、分类环形图。
4. 第四屏：预算预警、刚需结构、固定支出结构。
5. 第五屏：Top 10 大额支出、财务洞察。

具体建议：

- 指标卡在手机端每行最多两张，重要的“本月支出”可独占一行。
- 图表高度保持 260 到 320 像素。
- 表格只保留 4 到 6 个关键字段，描述字段允许换行。
- 避免在同一屏放置三个以上环形图。
- 全局“年月”筛选器固定在顶部，手机端切换月份时无需逐图修改。

## 11. 每月更新步骤

日常新增账单后，可只更新当月：

```powershell
python -m finance_tracker.bitable_dashboard --sync-month 2026-06
```

每月结束或历史数据发生调整时：

```powershell
python -m finance_tracker.bitable_dashboard --sync-summary
python -m finance_tracker.bitable_dashboard --audit
```

推荐流程：

1. 确认本地账本已完成记账和标签补充。
2. 如有明细待同步，先运行明细同步命令。
3. 运行 `--sync-month YYYY-MM` 更新当前月份汇总。
4. 打开飞书看板，确认全局年月筛选器选择正确。
5. 月末运行 `--sync-summary` 和 `--audit`，检查重复键与缺失月份。

## 12. 常见问题排查

### 汇总表为空

1. 运行 `python -m finance_tracker.bitable_dashboard --check`。
2. 运行 `python -m finance_tracker.bitable_dashboard --sync-summary`。
3. 确认本地 SQLite 中存在 `status='active'` 的流水。
4. 检查图表是否筛选了一个不存在数据的年月。
5. 检查组件数据源是否误选成原始明细表。

### 图表不更新

1. 先运行 `--sync-month YYYY-MM`。
2. 刷新飞书多维表格页面。
3. 检查图表是否锁定了旧月份。
4. 检查图表聚合方式：金额字段应使用“求和”，笔数字段通常使用“求和”。
5. 运行 `--audit` 检查缺失月份和重复业务键。

### 字段缺失或类型错误

1. 运行 `--check`，查看具体表名和字段名。
2. 字段名必须与汇总表定义完全一致。
3. 日期字段应为日期类型，金额和占比应为数字类型，标签和关联分类应为多选。
4. 若自动创建失败，根据命令返回的 `code`、`message`、`log_id` 手动建字段。
5. 不要修改业务键字段名称，例如“年月”“分类”“标签”“类型”。

### 权限不足

1. 在飞书开放平台为应用开启多维表格读取、创建表、创建字段、读取记录和写入记录权限。
2. 发布新的应用版本。
3. 将应用添加为该多维表格的协作者，并授予可编辑权限。
4. 重新运行 `--check`。
5. 排查时只提供 `code`、`message`、`log_id`，不要发送 App Secret 或 access_token。

## 13. 手动操作清单

以下操作必须在飞书端手动完成：

- 新建“个人财务分析看板”。
- 创建全局“年月”筛选器。
- 创建指标卡、折线图、柱状图、环形图、预算表格和洞察文本卡。
- 为每个组件选择数据来源表、筛选条件、聚合方式和排序。
- 配置颜色、条件格式、联动筛选和手机端布局。
- 配置看板查看与分享权限。

以下内容由本地命令维护：

- 11 张汇总表及字段检查。
- 汇总数据写入与幂等更新。
- 指定月份更新。
- 重复键、记录数和缺失月份审计。
"""


def generate_dashboard_guide(path=None):
    output_path = Path(
        path or PROJECT_ROOT / "docs" / "feishu_dashboard_setup.md"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(DASHBOARD_GUIDE.strip() + "\n", encoding="utf-8")
    return {
        "success": True,
        "code": 0,
        "message": "飞书财务看板搭建指南已生成。",
        "log_id": "",
        "path": str(output_path),
        "section_count": 7,
    }


class DashboardService:
    def __init__(self, client=None, config=None):
        self.config = config or get_feishu_config()
        if client is not None:
            self.client = client
        else:
            if lark is None:
                raise RuntimeError("lark-oapi is not installed.")
            self.client = (
                lark.Client.builder()
                .app_id(self.config.app_id)
                .app_secret(self.config.app_secret)
                .timeout(20)
                .build()
            )

    def list_tables(self):
        items = []
        page_token = None
        while True:
            builder = (
                ListAppTableRequest.builder()
                .app_token(self.config.bitable_app_token)
                .page_size(100)
            )
            if page_token:
                builder = builder.page_token(page_token)
            result = _api_result(
                self.client.bitable.v1.app_table.list(builder.build())
            )
            if not result["success"]:
                return {**result, "tables": []}
            data = result.pop("data", None)
            items.extend(
                {
                    "table_id": str(getattr(item, "table_id", "") or ""),
                    "name": str(getattr(item, "name", "") or ""),
                }
                for item in (getattr(data, "items", None) or [])
            )
            if not getattr(data, "has_more", False):
                return {**result, "tables": items}
            page_token = getattr(data, "page_token", None)

    def create_table(self, name):
        table = AppTable.builder().name(name).build()
        body = CreateAppTableRequestBody.builder().table(table).build()
        request = (
            CreateAppTableRequest.builder()
            .app_token(self.config.bitable_app_token)
            .request_body(body)
            .build()
        )
        result = _api_result(
            self.client.bitable.v1.app_table.create(request)
        )
        data = result.pop("data", None)
        result["table_id"] = str(getattr(data, "table_id", "") or "")
        return result

    def list_fields(self, table_id):
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
            result = _api_result(
                self.client.bitable.v1.app_table_field.list(builder.build())
            )
            if not result["success"]:
                return {**result, "fields": []}
            data = result.pop("data", None)
            fields.extend(
                {
                    "field_id": str(getattr(item, "field_id", "") or ""),
                    "field_name": str(getattr(item, "field_name", "") or ""),
                    "type": getattr(item, "type", None),
                    "ui_type": str(getattr(item, "ui_type", "") or ""),
                }
                for item in (getattr(data, "items", None) or [])
            )
            if not getattr(data, "has_more", False):
                return {**result, "fields": fields}
            page_token = getattr(data, "page_token", None)

    def create_field(self, table_id, field_name, field_type):
        field = (
            AppTableField.builder()
            .field_name(field_name)
            .type(field_type)
            .build()
        )
        request = (
            CreateAppTableFieldRequest.builder()
            .app_token(self.config.bitable_app_token)
            .table_id(table_id)
            .request_body(field)
            .build()
        )
        result = _api_result(
            self.client.bitable.v1.app_table_field.create(request)
        )
        result.pop("data", None)
        return result

    def list_records(self, table_id):
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
            result = _api_result(
                self.client.bitable.v1.app_table_record.list(builder.build())
            )
            if not result["success"]:
                return {**result, "records": []}
            data = result.pop("data", None)
            records.extend(
                {
                    "record_id": str(getattr(item, "record_id", "") or ""),
                    "fields": dict(getattr(item, "fields", {}) or {}),
                }
                for item in (getattr(data, "items", None) or [])
            )
            if not getattr(data, "has_more", False):
                return {**result, "records": records}
            page_token = getattr(data, "page_token", None)

    def batch_create(self, table_id, rows):
        return self._write_batches(table_id, rows, operation="create")

    def batch_update(self, table_id, rows):
        return self._write_batches(table_id, rows, operation="update")

    def batch_delete(self, table_id, record_ids):
        result = _ok("没有需要删除的记录。")
        deleted = 0
        for batch in _chunks(record_ids, 500):
            body = (
                BatchDeleteAppTableRecordRequestBody.builder()
                .records(batch)
                .build()
            )
            request = (
                BatchDeleteAppTableRecordRequest.builder()
                .app_token(self.config.bitable_app_token)
                .table_id(table_id)
                .request_body(body)
                .build()
            )
            result = _api_result(
                self.client.bitable.v1.app_table_record.batch_delete(request)
            )
            result.pop("data", None)
            if not result["success"]:
                return {**result, "deleted": deleted}
            deleted += len(batch)
        return {**result, "deleted": deleted}

    def _write_batches(self, table_id, rows, operation):
        count = 0
        result = _ok("没有需要写入的记录。")
        for batch in _chunks(rows, 500):
            records = [
                AppTableRecord.builder()
                .record_id(item.get("record_id"))
                .fields(item["fields"])
                .build()
                if operation == "update"
                else AppTableRecord.builder().fields(item["fields"]).build()
                for item in batch
            ]
            if operation == "update":
                body = (
                    BatchUpdateAppTableRecordRequestBody.builder()
                    .records(records)
                    .build()
                )
                request = (
                    BatchUpdateAppTableRecordRequest.builder()
                    .app_token(self.config.bitable_app_token)
                    .table_id(table_id)
                    .request_body(body)
                    .build()
                )
                response = self.client.bitable.v1.app_table_record.batch_update(
                    request
                )
            else:
                body = (
                    BatchCreateAppTableRecordRequestBody.builder()
                    .records(records)
                    .build()
                )
                request = (
                    BatchCreateAppTableRecordRequest.builder()
                    .app_token(self.config.bitable_app_token)
                    .table_id(table_id)
                    .request_body(body)
                    .build()
                )
                response = self.client.bitable.v1.app_table_record.batch_create(
                    request
                )
            result = _api_result(response)
            result.pop("data", None)
            if not result["success"]:
                return {**result, "count": count}
            count += len(batch)
        return {**result, "count": count}


def check_dashboard(service=None, auto_create=True):
    try:
        service = service or DashboardService()
        if not service.config.bitable_app_token:
            return _failure(
                "缺少 FEISHU_BITABLE_APP_TOKEN，无法访问多维表格应用。"
            )
        table_result = service.list_tables()
        if not table_result["success"]:
            return _with_manual_help(table_result)
        tables = {item["name"]: item["table_id"] for item in table_result["tables"]}
        results = []
        for name, definition in TABLE_DEFINITIONS.items():
            created = False
            table_id = tables.get(name)
            if not table_id and auto_create:
                create_result = service.create_table(name)
                if not create_result["success"]:
                    results.append(
                        _table_error(name, "create_table", create_result)
                    )
                    continue
                table_id = create_result["table_id"]
                tables[name] = table_id
                created = True
            if not table_id:
                results.append(
                    {
                        "table": name,
                        "success": False,
                        "exists": False,
                        "missing_fields": list(definition["fields"]),
                        "message": "汇总表不存在。",
                    }
                )
                continue
            field_result = service.list_fields(table_id)
            if not field_result["success"]:
                results.append(
                    _table_error(name, "list_fields", field_result, table_id)
                )
                continue
            existing = {
                item["field_name"]: item
                for item in field_result["fields"]
            }
            missing = [
                field
                for field in definition["fields"]
                if field not in existing
            ]
            create_errors = []
            if auto_create:
                for field in missing:
                    result = service.create_field(
                        table_id,
                        field,
                        definition["fields"][field],
                    )
                    if not result["success"]:
                        create_errors.append(
                            {
                                "field": field,
                                "code": result["code"],
                                "message": result["message"],
                                "log_id": result["log_id"],
                            }
                        )
                if missing and not create_errors:
                    missing = []
            invalid_types = [
                {
                    "field": field,
                    "expected_type": expected,
                    "actual_type": existing[field]["type"],
                }
                for field, expected in definition["fields"].items()
                if field in existing
                and existing[field]["type"] not in (None, expected)
            ]
            success = not missing and not create_errors and not invalid_types
            results.append(
                {
                    "table": name,
                    "table_id": table_id,
                    "success": success,
                    "exists": True,
                    "created": created,
                    "missing_fields": missing,
                    "invalid_field_types": invalid_types,
                    "create_errors": create_errors,
                    "message": (
                        "检查通过。"
                        if success
                        else _field_problem_message(
                            missing, invalid_types, create_errors
                        )
                    ),
                }
            )
        success = all(item["success"] for item in results)
        return {
            "success": success,
            "code": 0 if success else -1,
            "message": (
                "11 张财务汇总表检查通过。"
                if success
                else "部分汇总表或字段未准备完成。"
            ),
            "log_id": "",
            "tables": results,
            "manual_setup": (
                None if success else _manual_setup_instructions()
            ),
        }
    except Exception as exc:
        return _failure(f"{type(exc).__name__}: {_sanitize(str(exc))}")


def sync_summary(service=None):
    months = [item["month"] for item in analytics.get_monthly_trend()]
    return _sync(months, include_overview=True, service=service)


def sync_month(month, service=None):
    target_month = _month(month)
    return _sync([target_month], include_overview=False, service=service)


def audit_dashboard(service=None):
    service = service or DashboardService()
    check = check_dashboard(service=service, auto_create=False)
    if not check["success"]:
        return check
    expected_months = {
        item["month"] for item in analytics.get_monthly_trend()
    }
    result = []
    for table in check["tables"]:
        name = table["table"]
        definition = TABLE_DEFINITIONS[name]
        remote = service.list_records(table["table_id"])
        if not remote["success"]:
            result.append(_table_error(name, "list_records", remote))
            continue
        records = remote["records"]
        keys = [_record_key(item["fields"], definition) for item in records]
        duplicates = [
            _safe_key(key)
            for key, count in Counter(keys).items()
            if count > 1
        ]
        remote_months = {
            str(item["fields"].get("年月") or "")
            for item in records
            if item["fields"].get("年月")
        }
        missing_months = (
            sorted(expected_months - remote_months)
            if "年月" in definition["fields"]
            else []
        )
        result.append(
            {
                "table": name,
                "success": not duplicates,
                "record_count": len(records),
                "duplicate_key_count": len(duplicates),
                "duplicate_key_examples": duplicates[:10],
                "missing_months": missing_months,
            }
        )
    return {
        "success": all(item["success"] for item in result),
        "code": 0,
        "message": "汇总表审计完成。",
        "log_id": "",
        "expected_months": sorted(expected_months),
        "tables": result,
    }


def build_dashboard_rows(months=None, include_overview=True):
    months = list(months or [item["month"] for item in analytics.get_monthly_trend()])
    now = datetime.datetime.now()
    rows = {name: [] for name in TABLE_DEFINITIONS}
    if include_overview:
        overview = analytics.get_finance_overview()
        rows["财务总览表"].append(
            {
                "统计周期": "全部有效流水",
                "总收入": overview["total_income"],
                "总支出": overview["total_expense"],
                "净收入": overview["net_income"],
                "结余率": overview["savings_rate"],
                "交易笔数": overview["transaction_count"],
                "日均支出": overview["average_daily_expense"],
                "最大支出分类": overview["largest_expense_category"],
                "更新时间": _datetime_ms(now),
            }
        )
    monthly = {
        item["month"]: item for item in analytics.get_monthly_trend()
    }
    for month in months:
        item = monthly.get(month)
        if item:
            rows["月度汇总表"].append(
                {
                    "年月": month,
                    "收入": item["income"],
                    "支出": item["expense"],
                    "净收入": item["net_income"],
                    "结余率": item["savings_rate"],
                    "支出笔数": item["expense_count"],
                    "收入笔数": item["income_count"],
                }
            )

    for month in months:
        for item in analytics.get_category_expense_summary(month):
            rows["分类月度汇总表"].append(
                {
                    "年月": item["month"],
                    "分类": item["category"],
                    "金额": item["amount"],
                    "占比": item["share"],
                    "笔数": item["count"],
                    "日均金额": item["average_daily_amount"],
                }
            )
        for item in analytics.get_daily_expense_trend(month):
            rows["每日支出趋势表"].append(
                {
                    "日期": _date_ms(item["date"]),
                    "年月": item["month"],
                    "支出金额": item["expense"],
                    "支出笔数": item["expense_count"],
                    "7日移动平均": item["moving_average_7d"],
                }
            )
        for item in analytics.get_income_source_summary(month):
            rows["收入来源汇总表"].append(
                {
                    "年月": item["month"],
                    "收入分类": item["income_category"],
                    "金额": item["amount"],
                    "占比": item["share"],
                    "笔数": item["count"],
                }
            )
        for item in analytics.get_tag_summary(month):
            rows["标签分析表"].append(
                {
                    "年月": item["month"],
                    "标签": item["tag"],
                    "金额": item["amount"],
                    "笔数": item["count"],
                    "关联分类": item["related_categories"],
                }
            )
        for item in analytics.get_need_vs_want_summary(month):
            rows["刚需/非刚需分析表"].append(
                {
                    "年月": item["month"],
                    "类型": item["type"],
                    "金额": item["amount"],
                    "占比": item["share"],
                }
            )
        for item in analytics.get_fixed_vs_variable_summary(month):
            rows["固定/变动支出分析表"].append(
                {
                    "年月": item["month"],
                    "类型": item["type"],
                    "金额": item["amount"],
                    "占比": item["share"],
                }
            )
        for item in analytics.get_top_expenses(month):
            rows["大额支出表"].append(
                {
                    "年月": item["month"],
                    "日期": _date_ms(item["date"]),
                    "分类": item["category"],
                    "金额": item["amount"],
                    "描述": item["description"],
                    "标签": item["tags"],
                }
            )
        for item in analytics.get_budget_warning(month):
            rows["预算预警表"].append(
                {
                    "年月": item["month"],
                    "分类": item["category"],
                    "预算": item["budget"],
                    "已用": item["used"],
                    "剩余": item["remaining"],
                    "使用率": item["usage_rate"],
                    "状态": item["status"],
                }
            )
        insight = analytics.generate_finance_insights(month)
        primary = insight["primary_expense_category"] or {}
        rows["财务洞察表"].append(
            {
                "年月": month,
                "一句话总结": insight["summary"],
                "主要支出分类": (
                    f"{primary.get('category', '')} ¥{primary.get('amount', 0):.2f}"
                    if primary
                    else ""
                ),
                "异常支出": json.dumps(
                    insight["abnormal_expenses"],
                    ensure_ascii=False,
                ),
                "节省建议": insight["saving_advice"],
                "下月提醒": insight["next_month_reminder"],
                "生成时间": _datetime_ms(now),
            }
        )
    return rows


def _sync(months, include_overview, service=None):
    service = service or DashboardService()
    check = check_dashboard(service=service, auto_create=True)
    if not check["success"]:
        return check
    expected = build_dashboard_rows(months, include_overview)
    table_map = {
        item["table"]: item["table_id"] for item in check["tables"]
    }
    results = []
    for name, rows in expected.items():
        if not rows and not include_overview and name == "财务总览表":
            continue
        result = _reconcile_table(
            service,
            table_map[name],
            name,
            rows,
            months,
            full_scope=include_overview,
        )
        results.append(result)
        if not result["success"]:
            return {
                "success": False,
                "code": result["code"],
                "message": f"{name}同步失败：{result['message']}",
                "log_id": result["log_id"],
                "tables": results,
            }
    return {
        "success": True,
        "code": 0,
        "message": f"已同步 {len(results)} 张财务汇总表。",
        "log_id": "",
        "months": months,
        "tables": results,
    }


def _reconcile_table(service, table_id, name, expected_rows, months, full_scope):
    definition = TABLE_DEFINITIONS[name]
    remote = service.list_records(table_id)
    if not remote["success"]:
        return _table_sync_failure(name, remote)
    scoped = []
    unscoped = []
    for record in remote["records"]:
        record_month = str(record["fields"].get("年月") or "")
        in_scope = (
            full_scope
            or "年月" not in definition["fields"]
            or record_month in months
        )
        (scoped if in_scope else unscoped).append(record)

    existing_by_key = defaultdict(list)
    for record in scoped:
        existing_by_key[_record_key(record["fields"], definition)].append(record)
    expected_by_key = defaultdict(list)
    for fields in expected_rows:
        expected_by_key[_record_key(fields, definition)].append(fields)

    creates = []
    updates = []
    deletes = []
    for key in set(existing_by_key) | set(expected_by_key):
        existing = existing_by_key.get(key, [])
        wanted = expected_by_key.get(key, [])
        paired = min(len(existing), len(wanted))
        updates.extend(
            {
                "record_id": existing[index]["record_id"],
                "fields": wanted[index],
            }
            for index in range(paired)
        )
        creates.extend({"fields": item} for item in wanted[paired:])
        deletes.extend(item["record_id"] for item in existing[paired:])

    for operation, payload in (
        ("update", updates),
        ("create", creates),
        ("delete", deletes),
    ):
        if operation == "update":
            result = service.batch_update(table_id, payload)
        elif operation == "create":
            result = service.batch_create(table_id, payload)
        else:
            result = service.batch_delete(table_id, payload)
        if not result["success"]:
            return _table_sync_failure(name, result)
    return {
        "table": name,
        "success": True,
        "code": 0,
        "message": "同步完成。",
        "log_id": "",
        "expected": len(expected_rows),
        "created": len(creates),
        "updated": len(updates),
        "deleted": len(deletes),
        "preserved_out_of_scope": len(unscoped),
    }


def _record_key(fields, definition):
    return tuple(
        _key_value(fields.get(field), definition["fields"].get(field))
        for field in definition["key_fields"]
    )


def _key_value(value, field_type=None):
    if isinstance(value, list):
        return tuple(sorted(str(item) for item in value))
    if field_type == NUMBER:
        try:
            return round(float(value or 0), 2)
        except (TypeError, ValueError):
            return 0.0
    return str(value or "")


def _safe_key(key):
    return [
        list(value) if isinstance(value, tuple) else value
        for value in key
    ]


def _api_result(response):
    result = response_result(response)
    result["message"] = _sanitize(result["message"])
    return result


def _sanitize(message):
    text = str(message or "")
    for marker in ("tenant_access_token", "access_token", "Bearer"):
        if marker.lower() in text.lower():
            return "飞书 API 返回了包含敏感凭证的错误，内容已隐藏。"
    return text[:500]


def _manual_setup_instructions():
    return {
        "message": "请在同一个多维表格应用中手动创建以下数据表和字段，然后重新运行 --check。",
        "tables": {
            name: list(definition["fields"])
            for name, definition in TABLE_DEFINITIONS.items()
        },
    }


def _with_manual_help(result):
    return {
        **result,
        "manual_setup": _manual_setup_instructions(),
    }


def _table_error(name, stage, result, table_id=""):
    return {
        "table": name,
        "table_id": table_id,
        "success": False,
        "stage": stage,
        "code": result.get("code", -1),
        "message": result.get("message", "未知错误"),
        "log_id": result.get("log_id", ""),
    }


def _table_sync_failure(name, result):
    return {
        "table": name,
        "success": False,
        "code": result.get("code", -1),
        "message": result.get("message", "未知错误"),
        "log_id": result.get("log_id", ""),
    }


def _field_problem_message(missing, invalid, errors):
    parts = []
    if missing:
        parts.append("缺少字段：" + "、".join(missing))
    if invalid:
        parts.append(
            "字段类型不匹配：" + "、".join(item["field"] for item in invalid)
        )
    if errors:
        parts.append(
            "字段创建失败：" + "、".join(item["field"] for item in errors)
        )
    return "；".join(parts)


def _failure(message, code=-1, log_id=""):
    return {
        "success": False,
        "code": int(code),
        "message": _sanitize(message),
        "log_id": str(log_id or ""),
        "manual_setup": _manual_setup_instructions(),
    }


def _ok(message):
    return {
        "success": True,
        "code": 0,
        "message": message,
        "log_id": "",
    }


def _month(value):
    text = str(value or "")[:7]
    datetime.datetime.strptime(text, "%Y-%m")
    return text


def _date_ms(value):
    parsed = datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d")
    return int(parsed.timestamp() * 1000)


def _datetime_ms(value):
    return int(value.timestamp() * 1000)


def _chunks(items, size):
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start:start + size]


def main():
    parser = argparse.ArgumentParser(
        description="Synchronize local finance analytics to Feishu Bitable."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--sync-summary", action="store_true")
    group.add_argument("--sync-month")
    group.add_argument("--audit", action="store_true")
    group.add_argument("--generate-guide", action="store_true")
    args = parser.parse_args()
    if args.check:
        result = check_dashboard()
    elif args.sync_summary:
        result = sync_summary()
    elif args.sync_month:
        result = sync_month(args.sync_month)
    elif args.generate_guide:
        result = generate_dashboard_guide()
    else:
        result = audit_dashboard()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("success"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

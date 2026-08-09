"""确定性的经营与风控分析工具。"""

from __future__ import annotations

import re

from ict_agent.data import DatabaseScalar, DuckDBStore, QueryResult
from ict_agent.models import (
    BusinessDataCatalog,
    BusinessRecordSearchQuery,
    CaseType,
    DatasetCapability,
    EvidenceQuery,
    JsonScalar,
    ToolResult,
)
from ict_agent.semantic import SemanticCapability, capabilities_for, get_capability


class AnalysisInputError(ValueError):
    """分析参数不符合业务数据契约。"""


def _first_row(result: QueryResult) -> tuple[DatabaseScalar, ...]:
    if not result.rows:
        raise AnalysisInputError("没有找到符合条件的数据。")
    return result.rows[0]


def _number(value: DatabaseScalar) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return float(value)


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator != 0 else None


def _period(value: DatabaseScalar) -> str:
    return str(value).split("T", maxsplit=1)[0] if value is not None else ""


def _format_money(value: float) -> str:
    return f"{value:.2f} 元"


def _format_ratio(value: float | None) -> str:
    return "无法计算" if value is None else f"{value:.2%}"


def get_receivable_rule_features(store: DuckDBStore) -> QueryResult:
    """生成最新月末客户级应收规则特征，不作风险定性。"""

    return store.fetch(
        """
        WITH periods AS (
            SELECT
                MAX("快照时间") AS latest_date,
                COALESCE(
                    (
                        SELECT "快照时间"
                        FROM (SELECT DISTINCT "快照时间" FROM ar_snapshots)
                        ORDER BY "快照时间" DESC LIMIT 1 OFFSET 3
                    ),
                    MIN("快照时间")
                ) AS baseline_date
            FROM ar_snapshots
        ), latest_ar AS (
            SELECT
                a."客户编号" AS customer_id,
                MAX(a."客户名称") AS customer_name,
                SUM(a."应收金额") AS ar_amount,
                SUM(a."超期应收金额") AS overdue_amount,
                SUM(a."超期60天以上金额") AS overdue_60_amount,
                MAX(a."超期天数") AS max_overdue_days
            FROM ar_snapshots a, periods p
            WHERE a."快照时间" = p.latest_date
            GROUP BY a."客户编号"
        ), baseline_ar AS (
            SELECT
                a."客户编号" AS customer_id,
                SUM(a."超期应收金额") AS baseline_overdue_amount
            FROM ar_snapshots a, periods p
            WHERE a."快照时间" = p.baseline_date
            GROUP BY a."客户编号"
        ), recent_flows AS (
            SELECT
                customer_id,
                SUM(sales_amount) AS sales_3m,
                SUM(payment_amount) AS payments_3m
            FROM (
                SELECT
                    s."客户编号" AS customer_id,
                    SUM(s."销售金额_折扣后_含税") AS sales_amount,
                    0.0 AS payment_amount
                FROM sales s, periods p
                WHERE s."出库日期" > p.latest_date - INTERVAL '3 months'
                  AND s."出库日期" <= p.latest_date
                GROUP BY s."客户编号"

                UNION ALL

                SELECT
                    pay."客户编号" AS customer_id,
                    0.0 AS sales_amount,
                    SUM(pay."回款金额") AS payment_amount
                FROM payments pay, periods p
                WHERE pay."回款日期" > p.latest_date - INTERVAL '3 months'
                  AND pay."回款日期" <= p.latest_date
                GROUP BY pay."客户编号"
            ) flow_rows
            GROUP BY customer_id
        )
        SELECT
            p.latest_date AS observation_date,
            l.customer_id,
            l.customer_name,
            l.ar_amount,
            l.overdue_amount,
            l.overdue_60_amount,
            CASE WHEN l.ar_amount = 0 THEN NULL
                 ELSE l.overdue_60_amount / l.ar_amount END AS overdue_60_rate,
            COALESCE(l.max_overdue_days, 0) AS max_overdue_days,
            COALESCE(b.baseline_overdue_amount, 0) AS baseline_overdue_amount,
            l.overdue_amount - COALESCE(b.baseline_overdue_amount, 0)
                AS overdue_growth_3m,
            COALESCE(f.sales_3m, 0) AS sales_3m,
            COALESCE(f.payments_3m, 0) AS payments_3m,
            c."黑白名单状态" AS list_status,
            COALESCE(c."授信额度", 0) AS credit_limit
        FROM latest_ar l
        CROSS JOIN periods p
        LEFT JOIN baseline_ar b USING (customer_id)
        LEFT JOIN recent_flows f USING (customer_id)
        LEFT JOIN customer_credit c ON c."客户编号_中台" = l.customer_id
        ORDER BY l.overdue_60_amount DESC, l.ar_amount DESC
        """
    )


def get_inventory_rule_features(store: DuckDBStore) -> QueryResult:
    """生成最新季末物料与库存组织级库存规则特征，不作风险定性。"""

    return store.fetch(
        """
        WITH periods AS (
            SELECT
                MAX("快照日期") AS latest_date,
                (
                    SELECT "快照日期"
                    FROM (SELECT DISTINCT "快照日期" FROM inventory_snapshots)
                    ORDER BY "快照日期" DESC LIMIT 1 OFFSET 1
                ) AS previous_date
            FROM inventory_snapshots
        ), inventory AS (
            SELECT
                i."物料编码" AS material_code,
                i."库存组织名称" AS inventory_org,
                SUM(i."含税总价") FILTER (
                    WHERE i."快照日期" = p.latest_date
                ) AS inventory_amount,
                SUM(i."含税总价") FILTER (
                    WHERE i."快照日期" = p.previous_date
                ) AS previous_inventory_amount,
                SUM(CASE WHEN i."快照日期" = p.latest_date AND i."库龄" > 180
                         THEN i."含税总价" ELSE 0 END) AS stale_amount,
                SUM(CASE WHEN i."快照日期" = p.latest_date AND i."库龄" <= 60
                         THEN i."含税总价" ELSE 0 END) AS fresh_amount
            FROM inventory_snapshots i, periods p
            WHERE i."快照日期" IN (p.latest_date, p.previous_date)
            GROUP BY i."物料编码", i."库存组织名称"
        ), sales_flow AS (
            SELECT
                s."物料编码" AS material_code,
                s."库存组织名称" AS inventory_org,
                SUM(s."销售金额_折扣后_含税") FILTER (
                    WHERE s."出库日期" > p.latest_date - INTERVAL '3 months'
                      AND s."出库日期" <= p.latest_date
                ) AS sales_3m,
                SUM(s."销售金额_折扣后_含税") FILTER (
                    WHERE s."出库日期" > p.latest_date - INTERVAL '6 months'
                      AND s."出库日期" <= p.latest_date - INTERVAL '3 months'
                ) AS previous_sales_3m
            FROM sales s, periods p
            WHERE s."出库日期" > p.latest_date - INTERVAL '6 months'
              AND s."出库日期" <= p.latest_date
            GROUP BY s."物料编码", s."库存组织名称"
        )
        SELECT
            p.latest_date AS observation_date,
            i.material_code,
            i.inventory_org,
            COALESCE(i.inventory_amount, 0) AS inventory_amount,
            COALESCE(i.previous_inventory_amount, 0) AS previous_inventory_amount,
            COALESCE(i.inventory_amount, 0) - COALESCE(i.previous_inventory_amount, 0)
                AS inventory_growth,
            CASE WHEN COALESCE(i.previous_inventory_amount, 0) = 0 THEN NULL
                 ELSE (
                    COALESCE(i.inventory_amount, 0) - COALESCE(i.previous_inventory_amount, 0)
                 ) / ABS(i.previous_inventory_amount) END AS inventory_growth_rate,
            COALESCE(i.stale_amount, 0) AS stale_amount,
            CASE WHEN COALESCE(i.inventory_amount, 0) = 0 THEN NULL
                 ELSE COALESCE(i.stale_amount, 0) / i.inventory_amount END AS stale_rate,
            COALESCE(i.fresh_amount, 0) AS fresh_amount,
            COALESCE(s.sales_3m, 0) AS sales_3m,
            COALESCE(s.previous_sales_3m, 0) AS previous_sales_3m
        FROM inventory i
        CROSS JOIN periods p
        LEFT JOIN sales_flow s USING (material_code, inventory_org)
        WHERE COALESCE(i.inventory_amount, 0) > 0
        ORDER BY i.inventory_amount DESC
        """
    )


def get_business_overview(store: DuckDBStore) -> ToolResult:
    """返回全数据窗口经营规模与最新风险敞口。"""

    result = store.fetch(
        """
        SELECT
            (SELECT MIN("出库日期") FROM sales) AS start_date,
            (SELECT MAX("出库日期") FROM sales) AS end_date,
            (SELECT COALESCE(SUM("销售金额_折扣后_含税"), 0) FROM sales) AS sales_amount,
            (SELECT COALESCE(SUM("出库成本金额"), 0) FROM sales) AS sales_cost,
            (SELECT COALESCE(SUM("回款金额"), 0) FROM payments) AS payment_amount,
            (SELECT COALESCE(SUM("销售金额"), 0) FROM contracts) AS contract_amount,
            (
                SELECT COALESCE(SUM("应收金额"), 0)
                FROM ar_snapshots
                WHERE "快照时间" = (SELECT MAX("快照时间") FROM ar_snapshots)
            ) AS latest_ar,
            (
                SELECT COALESCE(SUM("超期应收金额"), 0)
                FROM ar_snapshots
                WHERE "快照时间" = (SELECT MAX("快照时间") FROM ar_snapshots)
            ) AS overdue_ar,
            (
                SELECT COALESCE(SUM("含税总价"), 0)
                FROM inventory_snapshots
                WHERE "快照日期" = (SELECT MAX("快照日期") FROM inventory_snapshots)
            ) AS latest_inventory
        """
    )
    row = _first_row(result)
    sales_amount = _number(row[2])
    sales_cost = _number(row[3])
    payment_amount = _number(row[4])
    contract_amount = _number(row[5])
    latest_ar = _number(row[6])
    overdue_ar = _number(row[7])
    latest_inventory = _number(row[8])
    gross_profit = sales_amount - sales_cost
    gross_margin = _ratio(gross_profit, sales_amount)
    overdue_rate = _ratio(overdue_ar, latest_ar)
    warnings = [] if sales_amount and latest_ar else ["部分比例因分母为 0 无法计算。"]
    period = f"{_period(row[0])} 至 {_period(row[1])}"
    return ToolResult(
        summary=(
            f"{period} 销售额 {_format_money(sales_amount)}，回款额 "
            f"{_format_money(payment_amount)}；最新应收 {_format_money(latest_ar)}，"
            f"其中超期 {_format_money(overdue_ar)}，超期率 {_format_ratio(overdue_rate)}。"
        ),
        columns=["指标", "值", "单位"],
        rows=[
            ["销售额", sales_amount, "元"],
            ["销售成本", sales_cost, "元"],
            ["含税粗算毛利", gross_profit, "元"],
            ["含税粗算毛利率", gross_margin, "比例"],
            ["回款额", payment_amount, "元"],
            ["合同签约额", contract_amount, "元"],
            ["最新应收余额", latest_ar, "元"],
            ["最新超期应收", overdue_ar, "元"],
            ["超期率", overdue_rate, "比例"],
            ["最新库存金额", latest_inventory, "元"],
        ],
        sources=["sales", "payments", "contracts", "ar_snapshots", "inventory_snapshots"],
        period=period,
        metric_definitions=[
            "销售额为销售流水含退货负值的含税折后金额之和。",
            "含税粗算毛利为销售额减出库成本。",
            "应收与库存只聚合各自最新快照。",
        ],
        warnings=warnings,
    )


def get_latest_ar_summary(store: DuckDBStore) -> ToolResult:
    """返回最新月末应收余额和各超期口径。"""

    result = store.fetch(
        """
        SELECT
            MAX("快照时间") AS snapshot_date,
            SUM("应收金额") FILTER (
                WHERE "快照时间" = (SELECT MAX("快照时间") FROM ar_snapshots)
            ) AS ar_amount,
            SUM("超期应收金额") FILTER (
                WHERE "快照时间" = (SELECT MAX("快照时间") FROM ar_snapshots)
            ) AS overdue_amount,
            SUM("超期30天以上金额") FILTER (
                WHERE "快照时间" = (SELECT MAX("快照时间") FROM ar_snapshots)
            ) AS overdue_30_amount,
            SUM("超期60天以上金额") FILTER (
                WHERE "快照时间" = (SELECT MAX("快照时间") FROM ar_snapshots)
            ) AS overdue_60_amount
        FROM ar_snapshots
        """
    )
    row = _first_row(result)
    ar_amount = _number(row[1])
    overdue_amount = _number(row[2])
    overdue_30_amount = _number(row[3])
    overdue_60_amount = _number(row[4])
    overdue_rate = _ratio(overdue_amount, ar_amount)
    overdue_30_rate = _ratio(overdue_30_amount, ar_amount)
    overdue_60_rate = _ratio(overdue_60_amount, ar_amount)
    warnings = [] if ar_amount else ["最新一期应收余额为 0，超期比例无法计算。"]
    period = _period(row[0])
    return ToolResult(
        summary=(
            f"截至 {period}，应收余额 {_format_money(ar_amount)}，超期应收 "
            f"{_format_money(overdue_amount)}，超期率 {_format_ratio(overdue_rate)}；"
            f"60 天以上超期率 {_format_ratio(overdue_60_rate)}。"
        ),
        columns=["指标", "值", "单位"],
        rows=[
            ["应收余额", ar_amount, "元"],
            ["超期应收", overdue_amount, "元"],
            ["超期率", overdue_rate, "比例"],
            ["30天以上超期", overdue_30_amount, "元"],
            ["30天以上超期率", overdue_30_rate, "比例"],
            ["60天以上超期", overdue_60_amount, "元"],
            ["60天以上超期率", overdue_60_rate, "比例"],
        ],
        sources=["ar_snapshots"],
        period=period,
        metric_definitions=[
            "最新一期取应收快照的最大快照时间。",
            "超期率为最新超期应收除以最新应收余额。",
        ],
        warnings=warnings,
    )


def get_ar_trend(store: DuckDBStore) -> ToolResult:
    """逐月返回应收、超期和深度超期趋势。"""

    result = store.fetch(
        """
        SELECT
            "快照时间" AS period,
            COALESCE(SUM("应收金额"), 0) AS ar_amount,
            COALESCE(SUM("超期应收金额"), 0) AS overdue_amount,
            CASE WHEN SUM("应收金额") = 0 THEN NULL
                 ELSE SUM("超期应收金额") / SUM("应收金额") END AS overdue_rate,
            CASE WHEN SUM("应收金额") = 0 THEN NULL
                 ELSE SUM("超期60天以上金额") / SUM("应收金额") END AS overdue_60_rate
        FROM ar_snapshots
        GROUP BY "快照时间"
        ORDER BY "快照时间"
        """
    )
    rows: list[list[JsonScalar]] = [
        [_period(row[0]), row[1], row[2], row[3], row[4]] for row in result.rows
    ]
    period = f"{rows[0][0]} 至 {rows[-1][0]}" if rows else ""
    return ToolResult(
        summary=f"应收趋势覆盖 {period}，共 {len(rows)} 个月末快照，每期独立聚合。",
        columns=["期间", "应收余额_元", "超期应收_元", "超期率", "60天以上超期率"],
        rows=rows,
        sources=["ar_snapshots"],
        period=period,
        metric_definitions=["每个快照期单独汇总，不跨期累加时点余额。"],
        warnings=[],
    )


def get_inventory_health(store: DuckDBStore) -> ToolResult:
    """返回最新季末库存金额、呆滞金额和库龄分桶。"""

    result = store.fetch(
        """
        WITH latest AS (
            SELECT * FROM inventory_snapshots
            WHERE "快照日期" = (SELECT MAX("快照日期") FROM inventory_snapshots)
        )
        SELECT
            MAX("快照日期") AS snapshot_date,
            COALESCE(SUM("含税总价"), 0) AS inventory_amount,
            COALESCE(SUM(CASE WHEN "库龄" > 180 THEN "含税总价" ELSE 0 END), 0)
                AS stale_amount,
            COALESCE(SUM(CASE WHEN COALESCE("是否超期", '') IN ('Y', '是', '1')
                              THEN "含税总价" ELSE 0 END), 0) AS overdue_loan_amount
        FROM latest
        """
    )
    row = _first_row(result)
    inventory_amount = _number(row[1])
    stale_amount = _number(row[2])
    stale_rate = _ratio(stale_amount, inventory_amount)
    bucket_result = store.fetch(
        """
        WITH latest AS (
            SELECT * FROM inventory_snapshots
            WHERE "快照日期" = (SELECT MAX("快照日期") FROM inventory_snapshots)
        ), bucketed AS (
            SELECT CASE
                WHEN "库龄" <= 30 THEN '0-30天'
                WHEN "库龄" <= 60 THEN '31-60天'
                WHEN "库龄" <= 90 THEN '61-90天'
                WHEN "库龄" <= 180 THEN '91-180天'
                WHEN "库龄" <= 365 THEN '181-365天'
                ELSE '365天以上'
            END AS age_bucket,
            CASE
                WHEN "库龄" <= 30 THEN 1
                WHEN "库龄" <= 60 THEN 2
                WHEN "库龄" <= 90 THEN 3
                WHEN "库龄" <= 180 THEN 4
                WHEN "库龄" <= 365 THEN 5
                ELSE 6
            END AS bucket_order,
            "含税总价",
            "物料编码"
            FROM latest
        )
        SELECT age_bucket, SUM("含税总价") AS amount, COUNT(DISTINCT "物料编码") AS sku_count
        FROM bucketed
        GROUP BY age_bucket, bucket_order
        ORDER BY bucket_order
        """
    )
    period = _period(row[0])
    warnings = ["库存快照没有客户维度，不能把某批库存归因到具体客户。"]
    return ToolResult(
        summary=(
            f"截至 {period}，库存总额 {_format_money(inventory_amount)}，180 天以上呆滞库存 "
            f"{_format_money(stale_amount)}，占比 {_format_ratio(stale_rate)}。"
        ),
        columns=["库龄区间", "库存金额_元", "SKU数"],
        rows=[list(row_values) for row_values in bucket_result.rows],
        sources=["inventory_snapshots"],
        period=period,
        metric_definitions=[
            "库存只取最新季末快照。",
            "呆滞库存为库龄严格大于 180 天的含税总价。",
            f"借物超期库存金额为 {_format_money(_number(row[3]))}。",
        ],
        warnings=warnings,
    )


def get_customer_ar_history(
    store: DuckDBStore, customer_id: str, *, months: int = 12
) -> ToolResult:
    """返回指定客户最近若干个月的应收与深度超期趋势。"""

    normalized_id = customer_id.strip().upper()
    if not re.fullmatch(r"C\d{3}", normalized_id):
        raise AnalysisInputError("客户编号必须采用 C015 这样的 C 加三位数字格式。")
    result = store.fetch(
        """
        SELECT
            "快照时间" AS period,
            SUM("应收金额") AS ar_amount,
            SUM("超期应收金额") AS overdue_amount,
            SUM("超期30天以上金额") AS overdue_30_amount,
            SUM("超期60天以上金额") AS overdue_60_amount,
            CASE WHEN SUM("应收金额") = 0 THEN NULL
                 ELSE SUM("超期应收金额") / SUM("应收金额") END AS overdue_rate,
            MAX("超期天数") AS max_overdue_days
        FROM ar_snapshots
        WHERE "客户编号" = ?
        GROUP BY "快照时间"
        ORDER BY "快照时间" DESC
        LIMIT ?
        """,
        [normalized_id, months],
    )
    rows = [
        [_period(row[0]), row[1], row[2], row[3], row[4], row[5], row[6]] for row in result.rows
    ]
    if not rows:
        raise AnalysisInputError(f"没有找到客户 {normalized_id} 的应收记录。")
    latest = rows[0]
    return ToolResult(
        summary=(
            f"{normalized_id} 截至 {latest[0]} 应收 {_format_money(_number(latest[1]))}，"
            f"超期 {_format_money(_number(latest[2]))}；趋势共 {len(rows)} 期。"
        ),
        columns=[
            "期间",
            "应收余额_元",
            "超期应收_元",
            "30天以上超期_元",
            "60天以上超期_元",
            "超期率",
            "最大超期天数",
        ],
        rows=rows,
        sources=["ar_snapshots"],
        period=f"{rows[-1][0]} 至 {rows[0][0]}",
        metric_definitions=["每个月末独立聚合，同一笔应收不跨期累加。"],
    )


def get_customer_flow_history(
    store: DuckDBStore, customer_id: str, *, months: int = 6
) -> ToolResult:
    """返回指定客户最近若干个月销售、回款和超期利息。"""

    normalized_id = customer_id.strip().upper()
    if not re.fullmatch(r"C\d{3}", normalized_id):
        raise AnalysisInputError("客户编号必须采用 C015 这样的 C 加三位数字格式。")
    result = store.fetch(
        """
        WITH latest AS (
            SELECT MAX("快照时间") AS latest_date FROM ar_snapshots
        ), months AS (
            SELECT DISTINCT date_trunc('month', "快照时间") AS month
            FROM ar_snapshots, latest
            WHERE "快照时间" > latest_date - (? * INTERVAL '1 month')
        ), sales_monthly AS (
            SELECT
                date_trunc('month', "出库日期") AS month,
                SUM("销售金额_折扣后_含税") AS sales_amount,
                SUM("销售金额_折扣后_含税" - "出库成本金额") AS gross_profit
            FROM sales
            WHERE "客户编号" = ?
            GROUP BY 1
        ), payment_monthly AS (
            SELECT
                date_trunc('month', "回款日期") AS month,
                SUM("回款金额") AS payment_amount,
                SUM("超期利息金额") AS overdue_interest,
                MAX("超期天数") AS max_payment_overdue_days
            FROM payments
            WHERE "客户编号" = ?
            GROUP BY 1
        )
        SELECT
            m.month,
            COALESCE(s.sales_amount, 0) AS sales_amount,
            COALESCE(p.payment_amount, 0) AS payment_amount,
            COALESCE(s.gross_profit, 0) AS gross_profit,
            COALESCE(p.overdue_interest, 0) AS overdue_interest,
            COALESCE(p.max_payment_overdue_days, 0) AS max_payment_overdue_days
        FROM months m
        LEFT JOIN sales_monthly s USING (month)
        LEFT JOIN payment_monthly p USING (month)
        ORDER BY m.month DESC
        """,
        [months, normalized_id, normalized_id],
    )
    rows = [[_period(row[0]), row[1], row[2], row[3], row[4], row[5]] for row in result.rows]
    period = f"{rows[-1][0]} 至 {rows[0][0]}" if rows else ""
    return ToolResult(
        summary=f"{normalized_id} 最近 {len(rows)} 个月的销售、回款和超期利息已对齐。",
        columns=[
            "月份",
            "销售额_元",
            "回款额_元",
            "含税粗算毛利_元",
            "超期利息_元",
            "回款最大超期天数",
        ],
        rows=rows,
        sources=["sales", "payments", "ar_snapshots"],
        period=period,
        metric_definitions=["销售和回款按自然月分别汇总，不把二者差额直接当作应收余额。"],
    )


def get_current_receivable_details(store: DuckDBStore, customer_id: str) -> ToolResult:
    """返回指定客户最新月末超期金额最大的应收订单明细。"""

    normalized_id = customer_id.strip().upper()
    if not re.fullmatch(r"C\d{3}", normalized_id):
        raise AnalysisInputError("客户编号必须采用 C015 这样的 C 加三位数字格式。")
    result = store.fetch(
        """
        SELECT
            MAX("快照时间") OVER () AS snapshot_date,
            COALESCE("合同号", '') AS contract_number,
            "销售订单号" AS sales_order_number,
            "物料编码" AS material_code,
            "最终承诺还款日期" AS final_promised_date,
            "是否展期" AS is_extended,
            MAX("超期天数") AS overdue_days,
            SUM("应收金额") AS ar_amount,
            SUM("超期应收金额") AS overdue_amount,
            SUM("超期30天以上金额") AS overdue_30_amount,
            SUM("超期60天以上金额") AS overdue_60_amount
        FROM ar_snapshots
        WHERE "客户编号" = ?
          AND "快照时间" = (SELECT MAX("快照时间") FROM ar_snapshots)
        GROUP BY "快照时间", "合同号", "销售订单号", "物料编码",
                 "最终承诺还款日期", "是否展期"
        ORDER BY overdue_60_amount DESC, overdue_amount DESC, ar_amount DESC
        LIMIT 50
        """,
        [normalized_id],
    )
    rows = [
        [
            _period(row[1]),
            row[2],
            row[3],
            _period(row[4]),
            row[5],
            row[6],
            row[7],
            row[8],
            row[9],
            row[10],
        ]
        for row in result.rows
    ]
    period = _period(result.rows[0][0]) if result.rows else ""
    return ToolResult(
        summary=f"{normalized_id} 截至 {period} 的高金额应收明细共返回 {len(rows)} 行。",
        columns=[
            "合同号",
            "销售订单号",
            "物料编码",
            "最终承诺还款日期",
            "是否展期",
            "超期天数",
            "应收金额_元",
            "超期应收_元",
            "30天以上超期_元",
            "60天以上超期_元",
        ],
        rows=rows,
        sources=["ar_snapshots"],
        period=period,
        metric_definitions=["仅取全表最新月末快照，按合同、订单和物料聚合。"],
        warnings=["只返回风险金额排序前 50 行，不能代表全部明细行数。"],
    )


def get_customer_extension_evidence(store: DuckDBStore, customer_id: str) -> ToolResult:
    """把客户当前应收按订单与历史展期动作精确匹配。"""

    normalized_id = customer_id.strip().upper()
    if not re.fullmatch(r"C\d{3}", normalized_id):
        raise AnalysisInputError("客户编号必须采用 C015 这样的 C 加三位数字格式。")
    result = store.fetch(
        """
        WITH latest AS (
            SELECT
                "客户编号" AS customer_id,
                COALESCE("合同号", '') AS contract_number,
                "销售订单号" AS sales_order_number,
                "物料编码" AS material_code,
                SUM("应收金额") AS ar_amount,
                SUM("超期应收金额") AS overdue_amount
            FROM ar_snapshots
            WHERE "客户编号" = ?
              AND "快照时间" = (SELECT MAX("快照时间") FROM ar_snapshots)
            GROUP BY 1, 2, 3, 4
        ), extension_actions AS (
            SELECT
                "客户编号" AS customer_id,
                COALESCE("合同号", '') AS contract_number,
                "销售订单号" AS sales_order_number,
                "物料编码" AS material_code,
                COUNT(DISTINCT "gkey") AS action_count,
                MAX("最终承诺还款日期") AS final_promised_date,
                MAX("快照时间") AS latest_extension_date
            FROM extensions
            WHERE "客户编号" = ?
            GROUP BY 1, 2, 3, 4
        )
        SELECT
            l.contract_number,
            l.sales_order_number,
            l.material_code,
            l.ar_amount,
            l.overdue_amount,
            COALESCE(e.action_count, 0) AS matched_extension_actions,
            e.final_promised_date,
            e.latest_extension_date
        FROM latest l
        LEFT JOIN extension_actions e USING (
            customer_id, contract_number, sales_order_number, material_code
        )
        WHERE e.action_count IS NOT NULL OR l.overdue_amount > 0
        ORDER BY matched_extension_actions DESC, l.overdue_amount DESC
        LIMIT 50
        """,
        [normalized_id, normalized_id],
    )
    rows = [
        [row[0], row[1], row[2], row[3], row[4], row[5], _period(row[6]), _period(row[7])]
        for row in result.rows
    ]
    matched = sum(1 for row in result.rows if _number(row[5]) > 0)
    period_result = store.fetch(
        """
        SELECT MIN("快照时间"), MAX("快照时间"), COUNT(DISTINCT "gkey")
        FROM extensions WHERE "客户编号" = ?
        """,
        [normalized_id],
    )
    period_row = _first_row(period_result)
    period = (
        f"{_period(period_row[0])} 至 {_period(period_row[1])}"
        if period_row[0] is not None
        else "无展期记录"
    )
    return ToolResult(
        summary=(
            f"{normalized_id} 历史展期动作 {int(_number(period_row[2]))} 次；"
            f"当前应收中有 {matched} 个订单物料组合匹配到历史展期。"
        ),
        columns=[
            "合同号",
            "销售订单号",
            "物料编码",
            "当前应收_元",
            "当前超期_元",
            "匹配展期动作数",
            "展期后最终承诺日",
            "最近展期记录日",
        ],
        rows=rows,
        sources=["ar_snapshots", "extensions"],
        period=period,
        metric_definitions=[
            "必须按客户、合同、销售订单和物料精确匹配，客户历史次数不能替代当前匹配。"
        ],
        warnings=["展期表没有审批人和审批状态，匹配到记录也不能证明审批手续完整。"],
    )


def get_customer_credit_context(store: DuckDBStore, customer_id: str) -> ToolResult:
    """返回客户当前授信、名单、财务概况与信用保险证据。"""

    normalized_id = customer_id.strip().upper()
    if not re.fullmatch(r"C\d{3}", normalized_id):
        raise AnalysisInputError("客户编号必须采用 C015 这样的 C 加三位数字格式。")
    result = store.fetch(
        """
        SELECT
            "客户名称", "授信额度", "黑白名单状态", "黑白名单原因",
            "黑白名单创建时间", "失信分级", "净资产", "净利润", "信用保险"
        FROM customer_credit WHERE "客户编号_中台" = ? LIMIT 1
        """,
        [normalized_id],
    )
    row = _first_row(result)
    labels = {0: "一般客户", 1: "白名单", 2: "黑名单", 3: "观察名单"}
    status = labels.get(int(_number(row[2])), f"未知状态 {row[2]}")
    return ToolResult(
        summary=(
            f"{normalized_id} {row[0]} 当前为{status}，授信额度 "
            f"{_format_money(_number(row[1]))}；名单创建时间 {_period(row[4]) or '缺失'}。"
        ),
        columns=["指标", "值", "单位"],
        rows=[
            ["客户名称", row[0], ""],
            ["名单状态", status, ""],
            ["名单原因", row[3], ""],
            ["名单创建时间", _period(row[4]), ""],
            ["授信额度", row[1], "元"],
            ["失信分级", row[5], ""],
            ["净资产", row[6], "元"],
            ["净利润", row[7], "元"],
            ["信用保险", row[8], ""],
        ],
        sources=["customer_credit"],
        period="当前主数据状态",
        metric_definitions=["授信额度是允许赊销额度，不是信用评分，也不能抵消已经发生的逾期。"],
        warnings=["客户授信只有当前状态，没有历史快照，名单状态可能滞后于最新经营行为。"],
    )


def get_customer_contract_context(store: DuckDBStore, customer_id: str) -> ToolResult:
    """返回客户当前应收所关联正式增值合同的闭环证据。"""

    normalized_id = customer_id.strip().upper()
    if not re.fullmatch(r"C\d{3}", normalized_id):
        raise AnalysisInputError("客户编号必须采用 C015 这样的 C 加三位数字格式。")
    result = store.fetch(
        """
        WITH customer_contracts AS (
            SELECT DISTINCT "合同号" AS contract_number
            FROM ar_snapshots
            WHERE "客户编号" = ?
              AND "快照时间" = (SELECT MAX("快照时间") FROM ar_snapshots)
              AND NULLIF("合同号", '') IS NOT NULL
        ), contract_base AS (
            SELECT
                c."合同编号" AS contract_number,
                MAX(c."合同状态") AS contract_status,
                SUM(c."销售金额") AS contract_amount,
                SUM(c."开票金额1") AS invoiced_amount,
                AVG(c."实际净毛利率_不含税") AS actual_margin_rate
            FROM contracts c
            JOIN customer_contracts cc ON cc.contract_number = c."合同编号"
            GROUP BY c."合同编号"
        )
        SELECT
            c.contract_number,
            c.contract_status,
            c.contract_amount,
            c.invoiced_amount,
            c.actual_margin_rate,
            COALESCE((SELECT SUM("销售金额_折扣后_含税") FROM sales s
                      WHERE s."合同号" = c.contract_number), 0) AS shipped_amount,
            COALESCE((SELECT SUM("回款金额") FROM payments p
                      WHERE p."合同号" = c.contract_number), 0) AS payment_amount,
            COALESCE((SELECT SUM("应收金额") FROM ar_snapshots a
                      WHERE a."合同号" = c.contract_number
                        AND a."快照时间" = (SELECT MAX("快照时间") FROM ar_snapshots)), 0)
                AS latest_ar_amount,
            COALESCE((SELECT SUM("超期应收金额") FROM ar_snapshots a
                      WHERE a."合同号" = c.contract_number
                        AND a."快照时间" = (SELECT MAX("快照时间") FROM ar_snapshots)), 0)
                AS latest_overdue_amount
        FROM contract_base c
        ORDER BY latest_overdue_amount DESC, contract_amount DESC
        LIMIT 30
        """,
        [normalized_id],
    )
    rows = [list(row) for row in result.rows]
    return ToolResult(
        summary=f"{normalized_id} 当前应收中可关联 {len(rows)} 个正式增值合同。",
        columns=[
            "合同号",
            "合同状态",
            "签约金额_元",
            "开票金额_元",
            "实际净毛利率",
            "出库金额_元",
            "回款金额_元",
            "最新应收_元",
            "最新超期_元",
        ],
        rows=rows,
        sources=["contracts", "sales", "payments", "ar_snapshots"],
        period="截至最新应收快照",
        metric_definitions=["只有正式合同号命中签约表的项目类业务才进入本结果。"],
        warnings=["数据没有项目验收记录，合同闭环只能作为间接证据。"],
    )


_WINDOW_MONTHS = {
    "latest": 1,
    "last_3_months": 3,
    "last_6_months": 6,
    "last_12_months": 12,
    "all": 24,
}
_WINDOW_QUARTERS = {
    "latest": 1,
    "last_3_months": 1,
    "last_6_months": 2,
    "last_12_months": 4,
    "all": 8,
}


def _validate_evidence_query(case_type: CaseType, query: EvidenceQuery) -> SemanticCapability:
    capability = get_capability(query.dataset, query.grain)
    if capability is None or capability.case_type != case_type:
        raise AnalysisInputError(f"{case_type} 案件不支持数据集 {query.dataset}/{query.grain}。")
    invalid_metrics = sorted(set(query.metrics) - set(capability.metrics))
    if invalid_metrics:
        raise AnalysisInputError(
            f"{query.dataset}/{query.grain} 不支持指标：{', '.join(invalid_metrics)}。"
        )
    if query.time_window not in capability.time_windows:
        raise AnalysisInputError(
            f"{query.dataset}/{query.grain} 不支持时间窗口 {query.time_window}。"
        )
    if query.sort_by is not None and query.sort_by not in query.metrics:
        raise AnalysisInputError("sort_by 必须同时出现在 metrics 中。")
    return capability


def _project_tool_result(
    result: ToolResult,
    query: EvidenceQuery,
    *,
    capability: SemanticCapability,
) -> ToolResult:
    source_columns = [
        *capability.dimension_columns,
        *(capability.source_metric_columns[item] for item in query.metrics),
    ]
    selected_columns = [
        *capability.dimension_columns,
        *(capability.output_metric_columns[item] for item in query.metrics),
    ]
    indices = [result.columns.index(column) for column in source_columns]
    rows = [[row[index] for index in indices] for row in result.rows]
    if query.sort_by is not None:
        sort_column = capability.output_metric_columns[query.sort_by]
        sort_index = selected_columns.index(sort_column)
        rows.sort(
            key=lambda row: (row[sort_index] is None, row[sort_index]),
            reverse=query.sort_direction == "desc",
        )
    rows = rows[: query.limit]
    return result.model_copy(update={"columns": selected_columns, "rows": rows})


def _credit_query_result(
    result: ToolResult,
    customer_id: str,
    query: EvidenceQuery,
    capability: SemanticCapability,
) -> ToolResult:
    values = {str(row[0]): row[1] for row in result.rows}
    columns = ["客户编号", *(capability.output_metric_columns[item] for item in query.metrics)]
    row = [customer_id, *(values[column] for column in columns[1:])]
    return result.model_copy(update={"columns": columns, "rows": [row]})


def query_business_evidence(
    store: DuckDBStore,
    case_type: CaseType,
    entity_context: dict[str, JsonScalar],
    query: EvidenceQuery,
) -> ToolResult:
    """按单一语义注册表执行当前案件范围内的受控证据查询。"""

    capability = _validate_evidence_query(case_type, query)
    key = (query.dataset, query.grain)
    if case_type == "ACCOUNTS_RECEIVABLE":
        normalized_id = str(entity_context.get("customer_id", "")).strip().upper()
        if not re.fullmatch(r"C\d{3}", normalized_id):
            raise AnalysisInputError("案件缺少合法客户编号。")
    else:
        material = str(entity_context.get("material_code", "")).strip()
        org = str(entity_context.get("inventory_org", "")).strip()
        if not material or not org:
            raise AnalysisInputError("库存案件缺少物料编码或库存组织。")

    if key == ("receivables", "month"):
        result = get_customer_ar_history(
            store, normalized_id, months=_WINDOW_MONTHS[query.time_window]
        )
    elif key == ("receivables", "order"):
        result = get_current_receivable_details(store, normalized_id)
    elif key == ("sales_payments", "month"):
        result = get_customer_flow_history(
            store, normalized_id, months=_WINDOW_MONTHS[query.time_window]
        )
    elif key == ("extensions", "order"):
        result = get_customer_extension_evidence(store, normalized_id)
    elif key == ("credit", "customer"):
        return _credit_query_result(
            get_customer_credit_context(store, normalized_id),
            normalized_id,
            query,
            capability,
        )
    elif key == ("contracts", "contract"):
        result = get_customer_contract_context(store, normalized_id)
    elif key == ("inventory", "quarter"):
        result = get_material_inventory_history(store, material, org)
        result = result.model_copy(
            update={"rows": result.rows[: _WINDOW_QUARTERS[query.time_window]]}
        )
    elif key == ("inventory", "age_bucket"):
        result = get_material_inventory_age_profile(store, material, org)
    elif key == ("sales", "month"):
        result = get_material_sales_context(
            store, material, org, months=_WINDOW_MONTHS[query.time_window]
        )
    else:  # pragma: no cover - 所有注册项必须有固定执行器
        raise AnalysisInputError("当前查询组合尚未实现。")
    return _project_tool_result(result, query, capability=capability)


def discover_evidence_capabilities(
    store: DuckDBStore,
    case_type: CaseType,
    entity_context: dict[str, JsonScalar],
    observation_date: str,
) -> BusinessDataCatalog:
    """用真实受控查询探测当前案件可用能力，不暴露 SQL 或物理字段。"""

    datasets = []
    for capability in capabilities_for(case_type):
        time_window = (
            "latest" if "latest" in capability.time_windows else capability.time_windows[0]
        )
        query = EvidenceQuery(
            dataset=capability.dataset,
            grain=capability.grain,
            metrics=list(capability.metrics),
            time_window=time_window,
            limit=1,
        )
        try:
            result = query_business_evidence(store, case_type, entity_context, query)
            available = True
            returned_rows = len(result.rows)
            period = result.period
        except AnalysisInputError:
            available = False
            returned_rows = 0
            period = None
        datasets.append(
            DatasetCapability(
                dataset=capability.dataset,
                grain=capability.grain,
                description=capability.description,
                metrics=list(capability.metrics),
                time_windows=list(capability.time_windows),
                available=available,
                returned_rows=returned_rows,
                period=period,
                limitations=list(capability.limitations),
            )
        )
    if case_type == "ACCOUNTS_RECEIVABLE":
        entity_scope = f"客户 {entity_context.get('customer_id', '')}"
    else:
        entity_scope = (
            f"物料 {entity_context.get('material_code', '')} / "
            f"库存组织 {entity_context.get('inventory_org', '')}"
        )
    return BusinessDataCatalog(
        case_type=case_type,
        entity_scope=entity_scope,
        observation_date=observation_date,
        datasets=datasets,
        global_rules=[
            "目录中的 available 来自当前数据快照的真实探测，不保证返回行数代表全部记录。",
            "所有搜索和查询自动限定当前案件主体，不能改查其他无关主体。",
            "金额、日期、比例和状态必须引用 query_business_evidence 返回的 evidence_id。",
            "模型不能提交 SQL、文件路径、正则表达式或代码。",
        ],
    )


def search_business_records(
    store: DuckDBStore,
    case_type: CaseType,
    entity_context: dict[str, JsonScalar],
    search: BusinessRecordSearchQuery,
) -> ToolResult:
    """在案件主体的关联记录内按业务标识做参数化包含搜索。"""

    query_text = search.query.strip()
    rows: list[list[JsonScalar]]
    if case_type == "ACCOUNTS_RECEIVABLE":
        customer_id = str(entity_context.get("customer_id", "")).strip().upper()
        if search.record_type == "customer":
            label = str(entity_context.get("customer_name", customer_id))
            rows = (
                [["customer", customer_id, label]]
                if query_text.lower() in f"{customer_id} {label}".lower()
                else []
            )
            sources = ["case_input"]
        else:
            column_by_type = {
                "contract": "合同号",
                "order": "销售订单号",
                "material": "物料编码",
            }
            column = column_by_type[search.record_type]
            result = store.fetch(
                f'''SELECT DISTINCT CAST("{column}" AS VARCHAR) AS record_id
                    FROM ar_snapshots
                    WHERE "客户编号" = ?
                      AND "快照时间" = (SELECT MAX("快照时间") FROM ar_snapshots)
                      AND contains(lower(CAST("{column}" AS VARCHAR)), lower(?))
                    ORDER BY record_id LIMIT ?''',
                [customer_id, query_text, search.limit],
            )
            rows = [[search.record_type, row[0], row[0]] for row in result.rows]
            sources = ["ar_snapshots"]
    else:
        material = str(entity_context.get("material_code", "")).strip()
        org = str(entity_context.get("inventory_org", "")).strip()
        if search.record_type == "material":
            rows = (
                [["material", material, material]] if query_text.lower() in material.lower() else []
            )
            sources = ["case_input"]
        else:
            column_by_type = {
                "customer": "客户编号",
                "contract": "合同号",
                "order": "销售订单号",
            }
            column = column_by_type[search.record_type]
            result = store.fetch(
                f'''SELECT DISTINCT CAST("{column}" AS VARCHAR) AS record_id
                    FROM sales
                    WHERE "物料编码" = ? AND "库存组织名称" = ?
                      AND contains(lower(CAST("{column}" AS VARCHAR)), lower(?))
                    ORDER BY record_id LIMIT ?''',
                [material, org, query_text, search.limit],
            )
            rows = [[search.record_type, row[0], row[0]] for row in result.rows]
            sources = ["sales"]
    return ToolResult(
        summary=f"在当前案件范围内找到 {len(rows)} 条 {search.record_type} 记录。",
        columns=["记录类型", "业务标识", "显示名称"],
        rows=rows[: search.limit],
        sources=sources,
        period="当前案件关联记录",
        metric_definitions=["搜索只匹配业务标识，不搜索文件名、物理表或任意数据库内容。"],
    )


def get_material_inventory_history(
    store: DuckDBStore,
    material_code: str,
    inventory_org: str,
) -> ToolResult:
    """返回指定物料与库存组织 8 个季末的金额和库龄结构。"""

    material = material_code.strip()
    org = inventory_org.strip()
    if not material or len(material) > 200 or not org or len(org) > 200:
        raise AnalysisInputError("物料编码和库存组织不能为空且不能超过 200 个字符。")
    result = store.fetch(
        """
        SELECT
            "快照日期",
            SUM("含税总价") AS inventory_amount,
            SUM(CASE WHEN "库龄" <= 60 THEN "含税总价" ELSE 0 END) AS fresh_amount,
            SUM(CASE WHEN "库龄" > 180 THEN "含税总价" ELSE 0 END) AS stale_amount,
            CASE WHEN SUM(ABS("数量")) = 0 THEN NULL
                 ELSE SUM("库龄" * ABS("数量")) / SUM(ABS("数量")) END AS weighted_age_days
        FROM inventory_snapshots
        WHERE "物料编码" = ? AND "库存组织名称" = ?
        GROUP BY "快照日期"
        ORDER BY "快照日期" DESC
        LIMIT 8
        """,
        [material, org],
    )
    rows = [[_period(row[0]), row[1], row[2], row[3], row[4]] for row in result.rows]
    if not rows:
        raise AnalysisInputError("没有找到指定物料与库存组织的库存记录。")
    return ToolResult(
        summary=(
            f"物料 {material} 截至 {rows[0][0]} 库存 "
            f"{_format_money(_number(rows[0][1]))}，历史共 {len(rows)} 个季末。"
        ),
        columns=["期间", "库存金额_元", "60天内库存_元", "180天以上库存_元", "加权库龄天数"],
        rows=rows,
        sources=["inventory_snapshots"],
        period=f"{rows[-1][0]} 至 {rows[0][0]}",
        metric_definitions=["库存按物料和库存组织逐季聚合；加权库龄以绝对数量加权。"],
    )


def get_material_inventory_age_profile(
    store: DuckDBStore,
    material_code: str,
    inventory_org: str,
) -> ToolResult:
    """返回指定物料与库存组织最新季末的库龄分桶。"""

    material = material_code.strip()
    org = inventory_org.strip()
    if not material or len(material) > 200 or not org or len(org) > 200:
        raise AnalysisInputError("物料编码和库存组织不能为空且不能超过 200 个字符。")
    result = store.fetch(
        """
        WITH latest AS (
            SELECT * FROM inventory_snapshots
            WHERE "快照日期" = (SELECT MAX("快照日期") FROM inventory_snapshots)
              AND "物料编码" = ? AND "库存组织名称" = ?
        ), bucketed AS (
            SELECT
                CASE WHEN "库龄" <= 30 THEN '0-30天'
                     WHEN "库龄" <= 60 THEN '31-60天'
                     WHEN "库龄" <= 90 THEN '61-90天'
                     WHEN "库龄" <= 180 THEN '91-180天'
                     WHEN "库龄" <= 365 THEN '181-365天'
                     ELSE '365天以上' END AS age_bucket,
                CASE WHEN "库龄" <= 30 THEN 1 WHEN "库龄" <= 60 THEN 2
                     WHEN "库龄" <= 90 THEN 3 WHEN "库龄" <= 180 THEN 4
                     WHEN "库龄" <= 365 THEN 5 ELSE 6 END AS bucket_order,
                "含税总价", "数量", "是否超期"
            FROM latest
        )
        SELECT
            age_bucket,
            SUM("含税总价") AS amount,
            SUM("数量") AS quantity,
            SUM(CASE WHEN COALESCE("是否超期", '') IN ('Y', '是', '1')
                     THEN "含税总价" ELSE 0 END) AS overdue_loan_amount
        FROM bucketed
        GROUP BY age_bucket, bucket_order
        ORDER BY bucket_order
        """,
        [material, org],
    )
    period_result = store.fetch('SELECT MAX("快照日期") FROM inventory_snapshots')
    period = _period(_first_row(period_result)[0])
    rows = [list(row) for row in result.rows]
    return ToolResult(
        summary=f"物料 {material} 截至 {period} 的最新库存分为 {len(rows)} 个库龄区间。",
        columns=["库龄区间", "库存金额_元", "数量", "借物超期金额_元"],
        rows=rows,
        sources=["inventory_snapshots"],
        period=period,
        metric_definitions=["各库龄分桶互不重叠，只聚合最新季末。"],
    )


def get_material_sales_context(
    store: DuckDBStore,
    material_code: str,
    inventory_org: str,
    *,
    months: int = 6,
) -> ToolResult:
    """返回指定物料与库存组织最近若干个月销售、退货和粗算毛利。"""

    material = material_code.strip()
    org = inventory_org.strip()
    if not material or len(material) > 200 or not org or len(org) > 200:
        raise AnalysisInputError("物料编码和库存组织不能为空且不能超过 200 个字符。")
    result = store.fetch(
        """
        WITH latest AS (SELECT MAX("快照日期") AS latest_date FROM inventory_snapshots)
        SELECT
            date_trunc('month', s."出库日期") AS month,
            SUM(s."销售金额_折扣后_含税") AS sales_amount,
            SUM(s."数量") AS net_quantity,
            SUM(CASE WHEN s."销售金额_折扣后_含税" < 0
                     THEN s."销售金额_折扣后_含税" ELSE 0 END) AS return_amount,
            SUM(s."销售金额_折扣后_含税" - s."出库成本金额") AS gross_profit,
            CASE WHEN SUM(s."销售金额_折扣后_含税") = 0 THEN NULL
                 ELSE SUM(s."销售金额_折扣后_含税" - s."出库成本金额")
                      / SUM(s."销售金额_折扣后_含税") END AS gross_margin
        FROM sales s, latest l
        WHERE s."物料编码" = ? AND s."库存组织名称" = ?
          AND s."出库日期" > l.latest_date - (? * INTERVAL '1 month')
          AND s."出库日期" <= l.latest_date
        GROUP BY 1
        ORDER BY 1 DESC
        """,
        [material, org, months],
    )
    rows = [[_period(row[0]), row[1], row[2], row[3], row[4], row[5]] for row in result.rows]
    period = f"{rows[-1][0]} 至 {rows[0][0]}" if rows else f"最近 {months} 个月无销售"
    return ToolResult(
        summary=f"物料 {material} 最近 {months} 个月返回 {len(rows)} 个有销售发生的月份。",
        columns=["月份", "销售额_元", "净数量", "退货金额_元", "含税粗算毛利_元", "粗算毛利率"],
        rows=rows,
        sources=["sales", "inventory_snapshots"],
        period=period,
        metric_definitions=["退货保留负金额；毛利为含税销售额减出库成本。"],
        warnings=["赛事数据不包含促销活动和下游库存，不能把销售变化归因到具体大促。"],
    )

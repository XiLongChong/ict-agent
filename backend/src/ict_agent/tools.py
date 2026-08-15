"""确定性的经营与风控分析工具。"""

from __future__ import annotations

import re

from ict_agent.business_type import business_type_condition
from ict_agent.data import DatabaseScalar, DuckDBStore, QueryResult
from ict_agent.models import (
    BusinessDataCatalog,
    BusinessRecordSearchQuery,
    BusinessType,
    DatasetCapability,
    EvidenceMetric,
    EvidenceQuery,
    InvestigationCaseInput,
    InvestigationProfile,
    JsonScalar,
    ToolResult,
)
from ict_agent.pretransaction import HistoricalOrderProfile
from ict_agent.semantic import SemanticCapability, capabilities_for, get_capability


class AnalysisInputError(ValueError):
    """分析参数不符合业务数据契约。"""


def validate_investigation_context(store: DuckDBStore, case: InvestigationCaseInput) -> None:
    """在模型启动前校验案件引用的固定快照与观察日期。"""

    snapshot_id = store.get_snapshot().snapshot_id
    if case.source_snapshot_id != snapshot_id:
        raise AnalysisInputError(
            "案件引用的数据快照与当前固定业务库不一致，请检查案件库和业务库配置。"
        )
    if case.source == "RULE_SCAN":
        date_column = "快照时间" if case.investigation_profile == "RECEIVABLES" else "快照日期"
        table = (
            "ar_snapshots" if case.investigation_profile == "RECEIVABLES" else "inventory_snapshots"
        )
        expected = _period(_first_row(store.fetch(f'SELECT MAX("{date_column}") FROM {table}'))[0])
        if case.observation_date != expected:
            raise AnalysisInputError(f"规则案件观察日期与固定业务快照不一致；当前应为 {expected}。")
    elif case.source == "PRE_TRANSACTION_SIMULATION":
        generated_at = str(case.subject_context.get("generated_at", ""))
        generated_date = generated_at.split("T", maxsplit=1)[0]
        if not case.subject_context.get("simulated") or not case.subject_context.get(
            "simulation_id"
        ):
            raise AnalysisInputError("事前交易案件缺少模拟订单身份。")
        if not generated_date or case.observation_date != generated_date:
            raise AnalysisInputError("事前交易案件观察日期与模拟订单生成日期不一致。")
    if any(signal.period != case.observation_date for signal in case.signals):
        raise AnalysisInputError("案件观察日期与来源信号期间不一致。")


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


def _fetch_bounded_detail(
    store: DuckDBStore,
    base_sql: str,
    parameters: list[object],
    *,
    limit: int,
    order_by_sql: str,
) -> tuple[QueryResult, int]:
    """先统计完整行数，再只读取可进入 ToolResult 的明细。"""

    total_result = store.fetch(
        f"SELECT COUNT(*) FROM ({base_sql}) AS governed_detail",
        parameters,
    )
    total_rows = int(_number(_first_row(total_result)[0]))
    if total_rows == 0:
        return QueryResult(columns=(), rows=()), 0
    result = store.fetch(
        f"SELECT * FROM ({base_sql}) AS governed_detail ORDER BY {order_by_sql} LIMIT ?",
        [*parameters, limit],
    )
    return result, total_rows


def _bounded_order_clause(
    sort_by: EvidenceMetric | None,
    sort_direction: str,
    columns: dict[EvidenceMetric, str],
    default: str,
) -> str:
    """把已校验语义指标映射为固定 SQL 排序表达式。"""

    if sort_by is None:
        return default
    direction = "ASC" if sort_direction == "asc" else "DESC"
    return f"{columns[sort_by]} {direction}"


def _format_money(value: float) -> str:
    return f"{value:.2f} 元"


def _format_ratio(value: float | None) -> str:
    return "无法计算" if value is None else f"{value:.2%}"


def list_customer_business_segments(
    store: DuckDBStore,
    *,
    customer_id: str | None = None,
    business_type: BusinessType | None = None,
) -> list[tuple[str, str, BusinessType, int]]:
    """列出有正向历史订单的客户×业务类型，供事前模拟入口选择。"""

    segments: list[tuple[str, str, BusinessType, int]] = []
    business_types: tuple[BusinessType, ...] = (
        (business_type,)
        if business_type is not None
        else ("DISTRIBUTION", "PROJECT", "SERVICE_CLOUD")
    )
    for current_type in business_types:
        condition = business_type_condition("s", current_type)
        customer_clause = 'AND s."客户编号" = ?' if customer_id else ""
        parameters: list[object] = [customer_id.strip().upper()] if customer_id else []
        result = store.fetch(
            f"""
            WITH order_totals AS (
                SELECT s."客户编号", MAX(s."客户名称") AS customer_name,
                       s."销售订单号",
                       SUM(s."销售金额_折扣后_含税") AS order_amount
                FROM sales s
                WHERE {condition} {customer_clause}
                GROUP BY s."客户编号", s."销售订单号"
            )
            SELECT "客户编号", MAX(customer_name) AS customer_name,
                   COUNT(*) AS positive_order_count
            FROM order_totals
            WHERE order_amount > 0
            GROUP BY "客户编号"
            ORDER BY "客户编号"
            """,
            parameters,
        )
        segments.extend(
            (str(row[0]), str(row[1]), current_type, int(row[2] or 0)) for row in result.rows
        )
    return segments


def get_historical_order_profile(
    store: DuckDBStore,
    customer_id: str,
    business_type: BusinessType,
    *,
    sample_seed: int = 0,
) -> HistoricalOrderProfile:
    """构造客户同业务类型的订单金额、毛利率与回款账龄历史画像。"""

    normalized_id = customer_id.strip().upper()
    if not re.fullmatch(r"C\d{3}", normalized_id):
        raise AnalysisInputError("客户编号必须采用 C015 这样的 C 加三位数字格式。")
    condition = business_type_condition("s", business_type)
    order_summary = store.fetch(
        f"""
        WITH order_totals AS (
            SELECT s."销售订单号" AS order_id,
                   MAX(s."客户名称") AS customer_name,
                   SUM(s."销售金额_折扣后_含税") AS order_amount,
                   CASE WHEN SUM(s."销售金额_折扣后_含税") = 0 THEN NULL
                        ELSE SUM(s."销售金额_折扣后_含税" - s."出库成本金额")
                             / SUM(s."销售金额_折扣后_含税") END AS gross_margin_rate
            FROM sales s
            WHERE s."客户编号" = ? AND {condition}
            GROUP BY s."销售订单号"
        )
        SELECT MAX(customer_name), COUNT(*),
               quantile_cont(order_amount, 0.25), quantile_cont(order_amount, 0.50),
               quantile_cont(order_amount, 0.75), quantile_cont(order_amount, 0.90),
               MAX(order_amount), median(gross_margin_rate)
        FROM order_totals
        WHERE order_amount > 0
        """,
        [normalized_id],
    )
    summary_row = _first_row(order_summary)
    order_count = int(_number(summary_row[1]))
    if order_count == 0:
        raise AnalysisInputError(f"客户 {normalized_id} 在 {business_type} 下没有正向历史订单。")
    sampled_order = store.fetch(
        f"""
        WITH order_totals AS (
            SELECT s."销售订单号" AS order_id,
                   SUM(s."销售金额_折扣后_含税") AS order_amount
            FROM sales s
            WHERE s."客户编号" = ? AND {condition}
            GROUP BY s."销售订单号"
        )
        SELECT order_amount
        FROM order_totals
        WHERE order_amount > 0
        ORDER BY hash(order_id, ?)
        LIMIT 1
        """,
        [normalized_id, sample_seed],
    )
    payment_summary = store.fetch(
        f"""
        WITH eligible_orders AS (
            SELECT DISTINCT s."客户编号" AS customer_id,
                            s."销售订单号" AS order_id
            FROM sales s
            WHERE s."客户编号" = ? AND {condition}
        )
        SELECT median(p."回款账龄")
        FROM payments p
        JOIN eligible_orders e
          ON e.customer_id = p."客户编号" AND e.order_id = p."销售订单号"
        WHERE p."回款账龄" IS NOT NULL AND p."回款账龄" >= 0
        """,
        [normalized_id],
    )
    payment_row = _first_row(payment_summary)
    distribution = {
        "p25_yuan": round(_number(summary_row[2]), 2),
        "median_yuan": round(_number(summary_row[3]), 2),
        "p75_yuan": round(_number(summary_row[4]), 2),
        "p90_yuan": round(_number(summary_row[5]), 2),
    }
    return HistoricalOrderProfile(
        customer_id=normalized_id,
        customer_name=str(summary_row[0]),
        business_type=business_type,
        historical_order_count=order_count,
        distribution_summary=distribution,
        maximum_order_amount=_number(summary_row[6]),
        sampled_order_amount=_number(_first_row(sampled_order)[0]),
        median_gross_margin_rate=(_number(summary_row[7]) if summary_row[7] is not None else None),
        median_payment_days=(_number(payment_row[0]) if payment_row[0] is not None else None),
        source_snapshot_id=store.get_snapshot().snapshot_id,
    )


def get_customer_business_profile_evidence(
    store: DuckDBStore,
    customer_id: str,
    business_type: BusinessType,
) -> ToolResult:
    """返回事前案件可引用的客户×业务类型历史基线。"""

    profile = get_historical_order_profile(store, customer_id, business_type)
    distribution = profile.distribution_summary
    median_payment_days = profile.median_payment_days
    median_margin_rate = profile.median_gross_margin_rate
    warnings: list[str] = []
    if profile.historical_order_count < 5:
        warnings.append("历史正订单少于 5 笔，分布稳定性有限。")
    if median_payment_days is None:
        warnings.append("当前业务类型未匹配到有效回款账龄。")
    if median_margin_rate is None:
        warnings.append("当前业务类型未形成可计算的历史毛利率。")
    return ToolResult(
        summary=(
            f"{profile.customer_id} 的 {business_type} 历史共有 "
            f"{profile.historical_order_count} 笔正向订单，"
            f"订单金额中位数 {_format_money(distribution['median_yuan'])}，"
            f"P90 {_format_money(distribution['p90_yuan'])}。"
        ),
        columns=[
            "客户编号",
            "业务类型",
            "历史订单数",
            "订单金额中位数_元",
            "订单金额P90_元",
            "回款账龄中位数_天",
            "历史毛利率中位数",
        ],
        rows=[
            [
                profile.customer_id,
                business_type,
                profile.historical_order_count,
                distribution["median_yuan"],
                distribution["p90_yuan"],
                median_payment_days,
                median_margin_rate,
            ]
        ],
        sources=["sales", "payments"],
        period="历史全量至当前数据快照",
        metric_definitions=[
            "订单金额按客户、业务类型和销售订单号聚合，保留退货后净额，只使用正向订单形成分布。",
            "P90 和中位数是历史比较基线，不是授信审批或违约阈值。",
        ],
        warnings=warnings,
    )


def get_pre_transaction_proposal_evidence(
    subject_context: dict[str, JsonScalar],
    business_type: BusinessType,
) -> ToolResult:
    """把案件库中的模拟交易输入转换为可引用证据。"""

    required = (
        "simulation_id",
        "customer_id",
        "amount_yuan",
        "proposed_term_days",
        "scenario",
    )
    if any(subject_context.get(key) in (None, "") for key in required):
        raise AnalysisInputError("事前案件缺少完整的模拟交易输入。")
    proposed_amount = subject_context["amount_yuan"]
    if not isinstance(proposed_amount, int | float) or isinstance(proposed_amount, bool):
        raise AnalysisInputError("事前案件的拟交易金额不是有效数字。")
    return ToolResult(
        summary=(
            f"模拟交易 {subject_context['simulation_id']} 拟向客户 "
            f"{subject_context['customer_id']} 开展 {business_type} 业务，"
            f"金额 {_format_money(float(proposed_amount))}。"
        ),
        columns=[
            "模拟交易编号",
            "客户编号",
            "业务类型",
            "场景",
            "拟交易金额_元",
            "拟账期天数",
            "预期毛利率",
        ],
        rows=[
            [
                subject_context["simulation_id"],
                subject_context["customer_id"],
                business_type,
                subject_context["scenario"],
                subject_context["amount_yuan"],
                subject_context["proposed_term_days"],
                subject_context.get("expected_margin_rate"),
            ]
        ],
        sources=["pre_transaction_simulations"],
        period=str(subject_context.get("generated_at", "")),
        metric_definitions=["模拟交易只用于事前调查演示，不写入真实销售、合同、应收或授信数据。"],
    )


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


def get_unpaid_sales_features(store: DuckDBStore) -> QueryResult:
    """生成客户级长期销售未回款特征（有正销售但无正回款的订单按账龄聚合）。"""

    return store.fetch(
        """
        WITH periods AS (
            SELECT MAX("快照时间") AS latest_date FROM ar_snapshots
        ), positive_sales AS (
            SELECT
                s."销售订单号" AS order_id,
                s."客户编号" AS customer_id,
                MAX(s."出库日期") AS ship_date,
                SUM(s."销售金额_折扣后_含税") AS sales_amount
            FROM sales s, periods p
            WHERE s."销售金额_折扣后_含税" > 0
            GROUP BY s."销售订单号", s."客户编号"
        ), positive_payments AS (
            SELECT DISTINCT "销售订单号" AS order_id
            FROM payments
            WHERE "回款金额" > 0
        ), unpaid AS (
            SELECT ps.*
            FROM positive_sales ps
            LEFT JOIN positive_payments pp USING (order_id)
            WHERE pp.order_id IS NULL
        ), customer_names AS (
            SELECT "客户编号" AS customer_id, MAX("客户名称") AS customer_name
            FROM ar_snapshots
            GROUP BY "客户编号"
        )
        SELECT
            u.customer_id,
            COALESCE(cn.customer_name, '') AS customer_name,
            COUNT(*) AS unpaid_order_count,
            SUM(u.sales_amount) AS unpaid_amount,
            SUM(
                CASE WHEN date_diff('day', u.ship_date, (SELECT latest_date FROM periods)) >= 90
                     THEN u.sales_amount ELSE 0 END
            ) AS unpaid_ge90_amount,
            MAX(date_diff('day', u.ship_date, (SELECT latest_date FROM periods))) AS max_unpaid_days
        FROM unpaid u
        LEFT JOIN customer_names cn USING (customer_id)
        GROUP BY u.customer_id, cn.customer_name
        ORDER BY unpaid_ge90_amount DESC
        """
    )


def get_inventory_zero_sales_features(store: DuckDBStore) -> QueryResult:
    """生成物料×库存组织级高库存但近三个月零销售特征。"""

    return store.fetch(
        """
        WITH periods AS (
            SELECT MAX("快照日期") AS latest_date FROM inventory_snapshots
        ), latest_inventory AS (
            SELECT
                i."物料编码" AS material_code,
                i."库存组织名称" AS inventory_org,
                SUM(i."含税总价") AS inventory_amount
            FROM inventory_snapshots i, periods p
            WHERE i."快照日期" = p.latest_date
            GROUP BY i."物料编码", i."库存组织名称"
        ), recent_sales AS (
            SELECT
                s."物料编码" AS material_code,
                s."库存组织名称" AS inventory_org,
                SUM(s."销售金额_折扣后_含税") AS sales_3m
            FROM sales s, periods p
            WHERE s."出库日期" > p.latest_date - INTERVAL '3 months'
              AND s."出库日期" <= p.latest_date
              AND s."销售金额_折扣后_含税" > 0
            GROUP BY s."物料编码", s."库存组织名称"
        )
        SELECT
            MAX(p.latest_date) AS observation_date,
            li.material_code,
            li.inventory_org,
            li.inventory_amount,
            COALESCE(rs.sales_3m, 0) AS sales_3m
        FROM latest_inventory li
        CROSS JOIN periods p
        LEFT JOIN recent_sales rs USING (material_code, inventory_org)
        WHERE li.inventory_amount > 0
        GROUP BY li.material_code, li.inventory_org, li.inventory_amount, rs.sales_3m
        ORDER BY li.inventory_amount DESC
        """
    )


def get_inventory_very_old_features(store: DuckDBStore) -> QueryResult:
    """生成物料×库存组织级超长库龄（365+ 天）库存特征。"""

    return store.fetch(
        """
        WITH latest AS (
            SELECT MAX("快照日期") AS latest_date FROM inventory_snapshots
        )
        SELECT
            MAX(p.latest_date) AS observation_date,
            i."物料编码" AS material_code,
            i."库存组织名称" AS inventory_org,
            SUM(i."含税总价") AS very_old_amount,
            SUM(i."数量") AS very_old_quantity
        FROM inventory_snapshots i, latest p
        WHERE i."快照日期" = p.latest_date
          AND i."库龄" > 365
        GROUP BY i."物料编码", i."库存组织名称"
        ORDER BY very_old_amount DESC
        """
    )


def get_extension_rule_features(store: DuckDBStore) -> QueryResult:
    """生成客户级展期次数特征（按 gkey 去重）。"""

    return store.fetch(
        """
        WITH customer_names AS (
            SELECT "客户编号" AS customer_id, MAX("客户名称") AS customer_name
            FROM ar_snapshots GROUP BY "客户编号"
        ), ext AS (
            SELECT
                "客户编号" AS customer_id,
                COUNT(DISTINCT gkey) AS extension_count
            FROM extensions
            GROUP BY "客户编号"
        )
        SELECT
            e.customer_id,
            COALESCE(cn.customer_name, '') AS customer_name,
            e.extension_count
        FROM ext e
        LEFT JOIN customer_names cn USING (customer_id)
        ORDER BY e.extension_count DESC
        """
    )


def get_penalty_interest_features(store: DuckDBStore) -> QueryResult:
    """生成客户级累计逾期罚息特征。"""

    return store.fetch(
        """
        WITH customer_names AS (
            SELECT "客户编号" AS customer_id, MAX("客户名称") AS customer_name
            FROM ar_snapshots GROUP BY "客户编号"
        ), pen AS (
            SELECT
                "客户编号" AS customer_id,
                COALESCE(SUM("超期利息金额"), 0) AS penalty_interest
            FROM payments
            GROUP BY "客户编号"
            HAVING COALESCE(SUM("超期利息金额"), 0) > 0
        )
        SELECT
            p.customer_id,
            COALESCE(cn.customer_name, '') AS customer_name,
            p.penalty_interest
        FROM pen p
        LEFT JOIN customer_names cn USING (customer_id)
        ORDER BY p.penalty_interest DESC
        """
    )


def get_inventory_stale_ratio_features(store: DuckDBStore) -> QueryResult:
    """生成物料×库存组织级呆滞占比（180 天以上）特征。"""

    return store.fetch(
        """
        WITH latest AS (
            SELECT MAX("快照日期") AS latest_date FROM inventory_snapshots
        ), agg AS (
            SELECT
                i."物料编码" AS material_code,
                i."库存组织名称" AS inventory_org,
                SUM(i."含税总价") AS inventory_amount,
                SUM(CASE WHEN i."库龄" > 180 THEN i."含税总价" ELSE 0 END) AS stale_amount
            FROM inventory_snapshots i, latest p
            WHERE i."快照日期" = p.latest_date
            GROUP BY i."物料编码", i."库存组织名称"
        )
        SELECT
            MAX(p.latest_date) AS observation_date,
            a.material_code,
            a.inventory_org,
            a.inventory_amount,
            a.stale_amount,
            CASE WHEN a.inventory_amount = 0 THEN NULL
                 ELSE a.stale_amount / a.inventory_amount END AS stale_rate
        FROM agg a, latest p
        GROUP BY a.material_code, a.inventory_org, a.inventory_amount, a.stale_amount
        ORDER BY stale_amount DESC
        """
    )


def get_inventory_overdue_stock_features(store: DuckDBStore) -> QueryResult:
    """生成物料×库存组织级超期库存（是否超期=Y）特征。"""

    return store.fetch(
        """
        WITH latest AS (
            SELECT MAX("快照日期") AS latest_date FROM inventory_snapshots
        ), agg AS (
            SELECT
                i."物料编码" AS material_code,
                i."库存组织名称" AS inventory_org,
                SUM(CASE WHEN i."是否超期" = 'Y' THEN i."含税总价" ELSE 0 END) AS overdue_amount,
                MAX(i."超期天数") AS max_overdue_days,
                COUNT(*) FILTER (WHERE i."是否超期" = 'Y') AS overdue_rows
            FROM inventory_snapshots i, latest p
            WHERE i."快照日期" = p.latest_date
            GROUP BY i."物料编码", i."库存组织名称"
        )
        SELECT
            MAX(p.latest_date) AS observation_date,
            a.material_code,
            a.inventory_org,
            a.overdue_amount,
            COALESCE(a.max_overdue_days, 0) AS max_overdue_days,
            a.overdue_rows
        FROM agg a, latest p
        GROUP BY a.material_code, a.inventory_org, a.overdue_amount,
                 a.max_overdue_days, a.overdue_rows
        ORDER BY a.overdue_amount DESC
        """
    )


def get_customer_return_features(store: DuckDBStore) -> QueryResult:
    """生成客户级退货占比特征（C1）。"""

    return store.fetch(
        """
        WITH names AS (
            SELECT "客户编号" AS customer_id, MAX("客户名称") AS customer_name
            FROM ar_snapshots GROUP BY "客户编号"
        ), agg AS (
            SELECT
                "客户编号" AS customer_id,
                SUM("销售金额_折扣后_含税") AS gross_sales,
                -SUM(CASE WHEN "销售金额_折扣后_含税" < 0
                          THEN "销售金额_折扣后_含税" ELSE 0 END) AS return_amount
            FROM sales
            GROUP BY "客户编号"
        )
        SELECT
            a.customer_id,
            COALESCE(n.customer_name, '') AS customer_name,
            a.gross_sales,
            a.return_amount
        FROM agg a
        LEFT JOIN names n USING (customer_id)
        """
    )


def get_negative_payment_features(store: DuckDBStore) -> QueryResult:
    """生成客户级负回款（冲销）占比特征（C3）。"""

    return store.fetch(
        """
        WITH names AS (
            SELECT "客户编号" AS customer_id, MAX("客户名称") AS customer_name
            FROM ar_snapshots GROUP BY "客户编号"
        ), agg AS (
            SELECT
                "客户编号" AS customer_id,
                SUM("回款金额") AS total_payment,
                -SUM(CASE WHEN "回款金额" < 0 THEN "回款金额" ELSE 0 END) AS negative_payment
            FROM payments
            GROUP BY "客户编号"
        )
        SELECT
            a.customer_id,
            COALESCE(n.customer_name, '') AS customer_name,
            a.total_payment,
            a.negative_payment
        FROM agg a
        LEFT JOIN names n USING (customer_id)
        """
    )


def get_aging_payment_features(store: DuckDBStore) -> QueryResult:
    """生成客户级超长账龄（>365 天）回款特征（C4）。"""

    return store.fetch(
        """
        WITH names AS (
            SELECT "客户编号" AS customer_id, MAX("客户名称") AS customer_name
            FROM ar_snapshots GROUP BY "客户编号"
        ), agg AS (
            SELECT
                "客户编号" AS customer_id,
                SUM(CASE WHEN "回款账龄" > 365 THEN "回款金额" ELSE 0 END) AS aging_amount
            FROM payments
            GROUP BY "客户编号"
        )
        SELECT
            a.customer_id,
            COALESCE(n.customer_name, '') AS customer_name,
            a.aging_amount
        FROM agg a
        LEFT JOIN names n USING (customer_id)
        """
    )


def get_negative_margin_features(store: DuckDBStore) -> QueryResult:
    """生成客户级负毛利亏损特征（D1，主键统一为 customer_id）。"""

    return store.fetch(
        """
        WITH customer_names AS (
            SELECT
                "客户名称" AS customer_name,
                CASE WHEN COUNT(DISTINCT "客户编号_中台") = 1
                     THEN MAX("客户编号_中台") END AS customer_id
            FROM customer_credit
            GROUP BY "客户名称"
        )
        SELECT
            n.customer_id,
            t.customer_name,
            t.margin_loss,
            t.contract_numbers
        FROM (
            SELECT
                "客户名称" AS customer_name,
                SUM(
                    "销售金额" * CASE WHEN "实际净毛利率_不含税" < 0
                                       THEN -"实际净毛利率_不含税" ELSE 0 END
                ) AS margin_loss,
                array_to_string(
                    list_sort(
                        list_distinct(list(NULLIF("合同编号", '') ORDER BY "合同编号"))
                    ),
                    '、'
                ) AS contract_numbers
            FROM contracts
            GROUP BY "客户名称"
        ) t
        LEFT JOIN customer_names n USING (customer_name)
        """
    )


def get_margin_optimistic_features(store: DuckDBStore) -> QueryResult:
    """生成合同级实估毛利严重高估特征（D2，金额加权聚合）。"""

    return store.fetch(
        """
        WITH contract_features AS (
            SELECT
                "合同编号" AS contract_number,
                MAX("客户名称") AS customer_name,
                SUM("销售金额") AS contract_amount,
                SUM("销售金额" * "实估毛利率_不含税") / NULLIF(SUM("销售金额"), 0)
                    AS weighted_est_margin,
                SUM("销售金额" * "实际净毛利率_不含税") / NULLIF(SUM("销售金额"), 0)
                    AS weighted_act_margin
            FROM contracts
            GROUP BY "合同编号"
        ), contract_links AS (
            SELECT "合同号" AS contract_number, "客户编号" AS customer_id
            FROM sales
            WHERE NULLIF("合同号", '') IS NOT NULL
              AND NULLIF("客户编号", '') IS NOT NULL
            UNION ALL
            SELECT "合同号" AS contract_number, "客户编号" AS customer_id
            FROM payments
            WHERE NULLIF("合同号", '') IS NOT NULL
              AND NULLIF("客户编号", '') IS NOT NULL
            UNION ALL
            SELECT "合同号" AS contract_number, "客户编号" AS customer_id
            FROM ar_snapshots
            WHERE NULLIF("合同号", '') IS NOT NULL
              AND NULLIF("客户编号", '') IS NOT NULL
        ), contract_link_summary AS (
            SELECT
                contract_number,
                CASE WHEN COUNT(DISTINCT customer_id) = 1
                     THEN MAX(customer_id) END AS customer_id,
                COUNT(DISTINCT customer_id) AS customer_count
            FROM contract_links
            GROUP BY contract_number
        ), customer_names AS (
            SELECT
                "客户名称" AS customer_name,
                CASE WHEN COUNT(DISTINCT "客户编号_中台") = 1
                     THEN MAX("客户编号_中台") END AS customer_id
            FROM customer_credit
            GROUP BY "客户名称"
        )
        SELECT
            t.contract_number,
            t.customer_name,
            CASE WHEN COALESCE(l.customer_count, 0) > 1 THEN NULL
                 ELSE COALESCE(l.customer_id, n.customer_id) END AS customer_id,
            t.contract_amount,
            t.weighted_est_margin,
            t.weighted_act_margin
        FROM contract_features t
        LEFT JOIN contract_link_summary l USING (contract_number)
        LEFT JOIN customer_names n USING (customer_name)
        """
    )


def get_term_overage_features(store: DuckDBStore) -> QueryResult:
    """生成客户级实际账期远超约定特征（D3）。"""

    return store.fetch(
        """
        WITH customer_names AS (
            SELECT
                "客户名称" AS customer_name,
                CASE WHEN COUNT(DISTINCT "客户编号_中台") = 1
                     THEN MAX("客户编号_中台") END AS customer_id
            FROM customer_credit
            GROUP BY "客户名称"
        )
        SELECT
            n.customer_id,
            t.customer_name,
            t.overage_contract_count,
            t.contract_amount,
            t.max_overage_days,
            t.contract_numbers
        FROM (
            SELECT
                "客户名称" AS customer_name,
                COUNT(*) AS overage_contract_count,
                SUM("销售金额") AS contract_amount,
                MAX("实际账期" - "合同文本账期") AS max_overage_days,
                array_to_string(
                    list_sort(
                        list_distinct(list(NULLIF("合同编号", '') ORDER BY "合同编号"))
                    ),
                    '、'
                ) AS contract_numbers
            FROM contracts
            WHERE "实际账期" IS NOT NULL AND "合同文本账期" IS NOT NULL
              AND "实际账期" - "合同文本账期" >= 120
            GROUP BY "客户名称"
        ) t
        LEFT JOIN customer_names n USING (customer_name)
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
    store: DuckDBStore,
    customer_id: str,
    *,
    months: int = 6,
    business_type: BusinessType | None = None,
) -> ToolResult:
    """返回指定客户最近若干个月销售、回款和超期利息。"""

    normalized_id = customer_id.strip().upper()
    if not re.fullmatch(r"C\d{3}", normalized_id):
        raise AnalysisInputError("客户编号必须采用 C015 这样的 C 加三位数字格式。")
    sales_condition = (
        business_type_condition("s", business_type) if business_type is not None else "TRUE"
    )
    payment_condition = "TRUE"
    if business_type is not None:
        linked_condition = business_type_condition("s", business_type)
        payment_condition = f"""EXISTS (
            SELECT 1 FROM sales s
            WHERE s."客户编号" = p."客户编号"
              AND s."销售订单号" = p."销售订单号"
              AND {linked_condition}
        )"""
    result = store.fetch(
        f"""
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
            FROM sales s
            WHERE s."客户编号" = ? AND {sales_condition}
            GROUP BY 1
        ), payment_monthly AS (
            SELECT
                date_trunc('month', "回款日期") AS month,
                SUM("回款金额") AS payment_amount,
                SUM("超期利息金额") AS overdue_interest,
                MAX("超期天数") AS max_payment_overdue_days
            FROM payments p
            WHERE p."客户编号" = ? AND {payment_condition}
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


def get_current_receivable_details(
    store: DuckDBStore,
    customer_id: str,
    *,
    limit: int = 200,
    sort_by: EvidenceMetric | None = None,
    sort_direction: str = "desc",
) -> ToolResult:
    """返回指定客户最新月末超期金额最大的应收订单明细。"""

    normalized_id = customer_id.strip().upper()
    if not re.fullmatch(r"C\d{3}", normalized_id):
        raise AnalysisInputError("客户编号必须采用 C015 这样的 C 加三位数字格式。")
    base_sql = """
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
        """
    order_by = _bounded_order_clause(
        sort_by,
        sort_direction,
        {
            "ar_amount": "ar_amount",
            "overdue_amount": "overdue_amount",
            "overdue_30_amount": "overdue_30_amount",
            "overdue_60_amount": "overdue_60_amount",
            "max_overdue_days": "overdue_days",
        },
        "overdue_60_amount DESC, overdue_amount DESC, ar_amount DESC",
    )
    result, total_rows = _fetch_bounded_detail(
        store,
        base_sql,
        [normalized_id],
        limit=limit,
        order_by_sql=order_by,
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
        summary=(
            f"{normalized_id} 截至 {period} 共找到 {total_rows} 行应收明细，"
            f"本次返回 {len(rows)} 行。"
        ),
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
        warnings=[],
        total_rows=total_rows,
        is_truncated=total_rows > len(rows),
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
    """返回客户全部可唯一关联正式合同的经济性与闭环证据。"""

    normalized_id = customer_id.strip().upper()
    if not re.fullmatch(r"C\d{3}", normalized_id):
        raise AnalysisInputError("客户编号必须采用 C015 这样的 C 加三位数字格式。")
    result = store.fetch(
        """
        WITH contract_base AS (
            SELECT
                c."合同编号" AS contract_number,
                MAX(c."客户名称") AS customer_name,
                MAX(c."合同状态") AS contract_status,
                SUM(c."销售金额") AS contract_amount,
                SUM(c."开票金额1") AS invoiced_amount,
                SUM(c."销售金额" * c."实估毛利率_不含税")
                    / NULLIF(SUM(c."销售金额"), 0) AS estimated_margin_rate,
                SUM(c."销售金额" * c."实际净毛利率_不含税")
                    / NULLIF(SUM(c."销售金额"), 0) AS actual_margin_rate,
                SUM(c."销售金额" * c."实际净毛利率_不含税") AS actual_gross_profit,
                MAX(c."合同文本账期") AS contract_term_days,
                MAX(c."实际账期") AS actual_term_days,
                MAX(c."实际账期" - c."合同文本账期") AS term_overage_days
            FROM contracts c
            GROUP BY c."合同编号"
        ), contract_links AS (
            SELECT "合同号" AS contract_number, "客户编号" AS customer_id FROM sales
            WHERE NULLIF("合同号", '') IS NOT NULL AND NULLIF("客户编号", '') IS NOT NULL
            UNION ALL
            SELECT "合同号", "客户编号" FROM payments
            WHERE NULLIF("合同号", '') IS NOT NULL AND NULLIF("客户编号", '') IS NOT NULL
            UNION ALL
            SELECT "合同号", "客户编号" FROM ar_snapshots
            WHERE NULLIF("合同号", '') IS NOT NULL AND NULLIF("客户编号", '') IS NOT NULL
        ), link_summary AS (
            SELECT contract_number,
                   CASE WHEN COUNT(DISTINCT customer_id) = 1 THEN MAX(customer_id) END customer_id,
                   COUNT(DISTINCT customer_id) AS customer_count
            FROM contract_links GROUP BY contract_number
        ), customer_names AS (
            SELECT "客户名称" customer_name,
                   CASE WHEN COUNT(DISTINCT "客户编号_中台") = 1
                        THEN MAX("客户编号_中台") END customer_id
            FROM customer_credit GROUP BY "客户名称"
        ), resolved AS (
            SELECT b.*,
                   CASE WHEN COALESCE(l.customer_count, 0) > 1 THEN NULL
                        ELSE COALESCE(l.customer_id, n.customer_id) END customer_id
            FROM contract_base b
            LEFT JOIN link_summary l USING (contract_number)
            LEFT JOIN customer_names n USING (customer_name)
        )
        SELECT
            c.contract_number,
            c.contract_status,
            c.contract_amount,
            c.invoiced_amount,
            c.estimated_margin_rate,
            c.actual_margin_rate,
            c.estimated_margin_rate - c.actual_margin_rate AS margin_gap,
            c.actual_gross_profit,
            c.contract_term_days,
            c.actual_term_days,
            c.term_overage_days,
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
        FROM resolved c
        WHERE c.customer_id = ?
        ORDER BY latest_overdue_amount DESC, contract_amount DESC
        """,
        [normalized_id],
    )
    rows = [list(row) for row in result.rows]
    return ToolResult(
        summary=f"{normalized_id} 可唯一关联 {len(rows)} 个正式增值合同。",
        columns=[
            "合同号",
            "合同状态",
            "签约金额_元",
            "开票金额_元",
            "实估毛利率",
            "实际净毛利率",
            "实估实际毛利率差",
            "实际净毛利_元",
            "合同文本账期_天",
            "实际账期_天",
            "账期超限_天",
            "出库金额_元",
            "回款金额_元",
            "最新应收_元",
            "最新超期_元",
        ],
        rows=rows,
        sources=["contracts", "sales", "payments", "ar_snapshots"],
        period="历史全量至固定数据快照",
        metric_definitions=[
            "合同优先按合同号唯一关联客户，无法交易关联时仅使用唯一客户名称映射。",
            "实估与实际毛利率均按合同销售金额加权，账期超限为实际账期减合同文本账期。",
        ],
        warnings=["数据没有项目验收记录，合同闭环只能作为间接证据。"],
    )


def get_customer_return_evidence(
    store: DuckDBStore,
    customer_id: str,
    *,
    limit: int = 200,
    sort_by: EvidenceMetric | None = None,
    sort_direction: str = "desc",
) -> ToolResult:
    """返回客户订单级销售与退货证据，保留退货负值口径。"""

    normalized_id = customer_id.strip().upper()
    if not re.fullmatch(r"C\d{3}", normalized_id):
        raise AnalysisInputError("客户编号必须采用 C015 这样的 C 加三位数字格式。")
    base_sql = """
        SELECT COALESCE("合同号", ''), "销售订单号",
               MIN("出库日期"), MAX("出库日期"),
               SUM("销售金额_折扣后_含税") AS net_sales_amount,
               -SUM(CASE WHEN "销售金额_折扣后_含税" < 0
                         THEN "销售金额_折扣后_含税" ELSE 0 END) AS return_amount,
               CASE WHEN SUM("销售金额_折扣后_含税") <= 0 THEN NULL
                    ELSE -SUM(CASE WHEN "销售金额_折扣后_含税" < 0
                                   THEN "销售金额_折扣后_含税" ELSE 0 END)
                         / SUM("销售金额_折扣后_含税") END AS return_ratio
        FROM sales WHERE "客户编号" = ?
        GROUP BY 1, 2
        """
    order_by = _bounded_order_clause(
        sort_by,
        sort_direction,
        {
            "gross_sales_amount": "net_sales_amount",
            "return_amount": "return_amount",
            "return_ratio": "return_ratio",
        },
        "return_amount DESC, net_sales_amount DESC",
    )
    result, total_rows = _fetch_bounded_detail(
        store,
        base_sql,
        [normalized_id],
        limit=limit,
        order_by_sql=order_by,
    )
    rows = [
        [row[0], row[1], _period(row[2]), _period(row[3]), row[4], row[5], row[6]]
        for row in result.rows
    ]
    period_row = _first_row(
        store.fetch(
            'SELECT MIN("出库日期"), MAX("出库日期") FROM sales WHERE "客户编号" = ?',
            [normalized_id],
        )
    )
    period = (
        f"{_period(period_row[0])} 至 {_period(period_row[1])}"
        if period_row[0] is not None
        else "无销售记录"
    )
    return ToolResult(
        summary=f"{normalized_id} 共找到 {total_rows} 个销售订单的退货证据。",
        columns=[
            "合同号",
            "销售订单号",
            "最早出库日",
            "最近出库日",
            "销售净额_元",
            "退货金额_元",
            "退货占比",
        ],
        rows=rows,
        sources=["sales"],
        period=period,
        metric_definitions=[
            "销售额沿用规则的退货后净额；退货金额把原始负销售额取反后展示，占比仅在净销售额大于0时计算。"
        ],
        total_rows=total_rows,
        is_truncated=total_rows > len(rows),
    )


def get_customer_return_summary(store: DuckDBStore, customer_id: str) -> ToolResult:
    """返回与异常退货规则完全一致的客户级汇总。"""

    normalized_id = customer_id.strip().upper()
    if not re.fullmatch(r"C\d{3}", normalized_id):
        raise AnalysisInputError("客户编号必须采用 C015 这样的 C 加三位数字格式。")
    result = store.fetch(
        """
        SELECT SUM("销售金额_折扣后_含税") AS net_sales,
               -SUM(CASE WHEN "销售金额_折扣后_含税" < 0
                         THEN "销售金额_折扣后_含税" ELSE 0 END) AS return_amount,
               CASE WHEN SUM("销售金额_折扣后_含税") <= 0 THEN NULL
                    ELSE -SUM(CASE WHEN "销售金额_折扣后_含税" < 0
                                   THEN "销售金额_折扣后_含税" ELSE 0 END)
                         / SUM("销售金额_折扣后_含税") END AS return_ratio,
               MIN("出库日期"), MAX("出库日期")
        FROM sales WHERE "客户编号" = ?
        """,
        [normalized_id],
    )
    row = _first_row(result)
    rows = [[normalized_id, row[0], row[1], row[2]]] if row[0] is not None else []
    period = f"{_period(row[3])} 至 {_period(row[4])}" if row[3] is not None else "无销售记录"
    return ToolResult(
        summary=f"{normalized_id} 的客户级销售与退货汇总已完整返回。",
        columns=["客户编号", "销售净额_元", "退货金额_元", "退货占比"],
        rows=rows,
        sources=["sales"],
        period=period,
        metric_definitions=["与规则一致：销售分母为包含退货负值的客户历史销售净额。"],
    )


def get_customer_payment_risk_evidence(
    store: DuckDBStore,
    customer_id: str,
    *,
    limit: int = 200,
    sort_by: EvidenceMetric | None = None,
    sort_direction: str = "desc",
) -> ToolResult:
    """返回客户订单级负回款、超长账龄与罚息证据。"""

    normalized_id = customer_id.strip().upper()
    if not re.fullmatch(r"C\d{3}", normalized_id):
        raise AnalysisInputError("客户编号必须采用 C015 这样的 C 加三位数字格式。")
    base_sql = """
        SELECT COALESCE("合同号", ''), "销售订单号",
               MIN("回款日期"), MAX("回款日期"),
               SUM("回款金额") AS net_payment_amount,
               SUM(CASE WHEN "回款金额" > 0 THEN "回款金额" ELSE 0 END)
                   AS positive_payment_amount,
               -SUM(CASE WHEN "回款金额" < 0 THEN "回款金额" ELSE 0 END)
                   AS negative_payment_amount,
               CASE WHEN SUM("回款金额") <= 0 THEN NULL
                    ELSE -SUM(CASE WHEN "回款金额" < 0 THEN "回款金额" ELSE 0 END)
                         / SUM("回款金额") END AS negative_payment_ratio,
               SUM(CASE WHEN "回款账龄" > 365 THEN "回款金额" ELSE 0 END)
                   AS over_365_payment_amount,
               SUM("超期利息金额") AS overdue_interest,
               MAX("超期天数") AS max_overdue_days,
               MAX("回款账龄") AS max_payment_age_days
        FROM payments WHERE "客户编号" = ?
        GROUP BY 1, 2
        """
    order_by = _bounded_order_clause(
        sort_by,
        sort_direction,
        {
            "payment_amount": "net_payment_amount",
            "positive_payment_amount": "positive_payment_amount",
            "negative_payment_amount": "negative_payment_amount",
            "negative_payment_ratio": "negative_payment_ratio",
            "over_365_payment_amount": "over_365_payment_amount",
            "overdue_interest": "overdue_interest",
            "max_payment_overdue_days": "max_overdue_days",
            "max_payment_age_days": "max_payment_age_days",
        },
        "negative_payment_amount DESC, over_365_payment_amount DESC, overdue_interest DESC",
    )
    result, total_rows = _fetch_bounded_detail(
        store,
        base_sql,
        [normalized_id],
        limit=limit,
        order_by_sql=order_by,
    )
    rows = [[row[0], row[1], _period(row[2]), _period(row[3]), *row[4:]] for row in result.rows]
    period_row = _first_row(
        store.fetch(
            'SELECT MIN("回款日期"), MAX("回款日期") FROM payments WHERE "客户编号" = ?',
            [normalized_id],
        )
    )
    period = (
        f"{_period(period_row[0])} 至 {_period(period_row[1])}"
        if period_row[0] is not None
        else "无回款记录"
    )
    return ToolResult(
        summary=f"{normalized_id} 共找到 {total_rows} 个订单的回款风险证据。",
        columns=[
            "合同号",
            "销售订单号",
            "最早回款日",
            "最近回款日",
            "净回款额_元",
            "正向回款额_元",
            "负回款金额_元",
            "负回款占比",
            "365天以上回款额_元",
            "超期利息_元",
            "最大超期天数",
            "最大回款账龄_天",
        ],
        rows=rows,
        sources=["payments"],
        period=period,
        metric_definitions=[
            "负回款按原始负金额取反后展示；365天以上回款严格使用回款账龄大于365天。"
        ],
        warnings=["负回款只能证明业务表存在冲销，不能推断银行到账或财务核销状态。"],
        total_rows=total_rows,
        is_truncated=total_rows > len(rows),
    )


def get_customer_payment_risk_summary(store: DuckDBStore, customer_id: str) -> ToolResult:
    """返回与回款风险规则一致的完整客户级汇总。"""

    normalized_id = customer_id.strip().upper()
    if not re.fullmatch(r"C\d{3}", normalized_id):
        raise AnalysisInputError("客户编号必须采用 C015 这样的 C 加三位数字格式。")
    result = store.fetch(
        """
        SELECT SUM("回款金额"),
               SUM(CASE WHEN "回款金额" > 0 THEN "回款金额" ELSE 0 END),
               -SUM(CASE WHEN "回款金额" < 0 THEN "回款金额" ELSE 0 END),
               CASE WHEN SUM("回款金额") <= 0 THEN NULL
                    ELSE -SUM(CASE WHEN "回款金额" < 0 THEN "回款金额" ELSE 0 END)
                         / SUM("回款金额") END,
               SUM(CASE WHEN "回款账龄" > 365 THEN "回款金额" ELSE 0 END),
               SUM("超期利息金额"), MAX("超期天数"), MAX("回款账龄"),
               MIN("回款日期"), MAX("回款日期")
        FROM payments WHERE "客户编号" = ?
        """,
        [normalized_id],
    )
    row = _first_row(result)
    rows = [[normalized_id, *row[:8]]] if row[0] is not None else []
    period = f"{_period(row[8])} 至 {_period(row[9])}" if row[8] is not None else "无回款记录"
    return ToolResult(
        summary=f"{normalized_id} 的客户级回款风险汇总已完整返回。",
        columns=[
            "客户编号",
            "净回款额_元",
            "正向回款额_元",
            "负回款金额_元",
            "负回款占比",
            "365天以上回款额_元",
            "超期利息_元",
            "最大超期天数",
            "最大回款账龄_天",
        ],
        rows=rows,
        sources=["payments"],
        period=period,
        metric_definitions=["负回款、365天以上账龄和比例均沿用现行规则口径。"],
    )


def get_customer_collection_evidence(
    store: DuckDBStore,
    customer_id: str,
    *,
    limit: int = 200,
    sort_by: EvidenceMetric | None = None,
    sort_direction: str = "desc",
) -> ToolResult:
    """按冻结规则核对有正销售但不存在正向回款的订单。"""

    normalized_id = customer_id.strip().upper()
    if not re.fullmatch(r"C\d{3}", normalized_id):
        raise AnalysisInputError("客户编号必须采用 C015 这样的 C 加三位数字格式。")
    base_sql = """
        WITH observation AS (SELECT MAX("快照时间") latest_date FROM ar_snapshots),
        positive_sales AS (
            SELECT COALESCE("合同号", '') contract_number, "销售订单号" order_id,
                   MAX("出库日期") ship_date,
                   SUM(CASE WHEN "销售金额_折扣后_含税" > 0
                            THEN "销售金额_折扣后_含税" ELSE 0 END) sales_amount
            FROM sales WHERE "客户编号" = ? GROUP BY 1, 2
        ), positive_payments AS (
            SELECT "销售订单号" order_id,
                   SUM(CASE WHEN "回款金额" > 0 THEN "回款金额" ELSE 0 END) payment_amount
            FROM payments WHERE "客户编号" = ? GROUP BY 1
        )
        SELECT s.contract_number, s.order_id, s.ship_date,
               CASE WHEN COALESCE(p.payment_amount, 0) > 0 THEN 'Y' ELSE 'N' END
                   AS has_positive_payment,
               s.sales_amount, COALESCE(p.payment_amount, 0) AS positive_payment_amount,
               CASE WHEN COALESCE(p.payment_amount, 0) > 0 THEN 0 ELSE s.sales_amount END
                   AS unpaid_amount,
               date_diff('day', s.ship_date, o.latest_date) AS unpaid_days
        FROM positive_sales s
        CROSS JOIN observation o
        LEFT JOIN positive_payments p USING (order_id)
        WHERE s.sales_amount > 0
        """
    order_by = _bounded_order_clause(
        sort_by,
        sort_direction,
        {
            "sales_amount": "sales_amount",
            "payment_amount": "positive_payment_amount",
            "unpaid_amount": "unpaid_amount",
            "max_unpaid_days": "unpaid_days",
        },
        "unpaid_amount DESC, unpaid_days DESC",
    )
    result, total_rows = _fetch_bounded_detail(
        store,
        base_sql,
        [normalized_id, normalized_id],
        limit=limit,
        order_by_sql=order_by,
    )
    rows = [[row[0], row[1], _period(row[2]), *row[3:]] for row in result.rows]
    return ToolResult(
        summary=f"{normalized_id} 共核对 {total_rows} 个正向销售订单的正向回款状态。",
        columns=[
            "合同号",
            "销售订单号",
            "最近出库日",
            "是否存在正向回款",
            "正向销售额_元",
            "正向回款额_元",
            "未回款销售额_元",
            "未回款天数",
        ],
        rows=rows,
        sources=["sales", "payments", "ar_snapshots"],
        period="历史全量至最新应收快照",
        metric_definitions=["沿用规则口径：订单存在任意正向回款即视为已回款；未做发票级回款分摊。"],
        total_rows=total_rows,
        is_truncated=total_rows > len(rows),
    )


def get_customer_collection_summary(store: DuckDBStore, customer_id: str) -> ToolResult:
    """返回长期未回款规则使用的完整客户级汇总。"""

    normalized_id = customer_id.strip().upper()
    if not re.fullmatch(r"C\d{3}", normalized_id):
        raise AnalysisInputError("客户编号必须采用 C015 这样的 C 加三位数字格式。")
    result = store.fetch(
        """
        WITH observation AS (SELECT MAX("快照时间") latest_date FROM ar_snapshots),
        positive_sales AS (
            SELECT "销售订单号" order_id, MAX("出库日期") ship_date,
                   SUM(CASE WHEN "销售金额_折扣后_含税" > 0
                            THEN "销售金额_折扣后_含税" ELSE 0 END) sales_amount
            FROM sales WHERE "客户编号" = ? GROUP BY 1
        ), positive_payments AS (
            SELECT "销售订单号" order_id,
                   SUM(CASE WHEN "回款金额" > 0 THEN "回款金额" ELSE 0 END) payment_amount
            FROM payments WHERE "客户编号" = ? GROUP BY 1
        )
        SELECT SUM(s.sales_amount), COALESCE(SUM(p.payment_amount), 0),
               SUM(CASE WHEN COALESCE(p.payment_amount, 0) > 0 THEN 0 ELSE s.sales_amount END),
               MAX(CASE WHEN COALESCE(p.payment_amount, 0) > 0 THEN 0
                        ELSE date_diff('day', s.ship_date, o.latest_date) END)
        FROM positive_sales s CROSS JOIN observation o
        LEFT JOIN positive_payments p USING (order_id)
        WHERE s.sales_amount > 0
        """,
        [normalized_id, normalized_id],
    )
    row = _first_row(result)
    rows = [[normalized_id, *row]] if row[0] is not None else []
    return ToolResult(
        summary=f"{normalized_id} 的长期未回款客户级汇总已完整返回。",
        columns=[
            "客户编号",
            "正向销售额_元",
            "正向回款额_元",
            "未回款销售额_元",
            "最大未回款天数",
        ],
        rows=rows,
        sources=["sales", "payments", "ar_snapshots"],
        period="历史全量至最新应收快照",
        metric_definitions=["订单存在任意正向回款即视为已回款，与现行规则保持一致。"],
    )


def get_material_overdue_inventory_evidence(
    store: DuckDBStore, material_code: str, inventory_org: str
) -> ToolResult:
    """返回物料与库存组织最新季末的超期库存记录。"""

    material = material_code.strip()
    org = inventory_org.strip()
    if not material or len(material) > 200 or not org or len(org) > 200:
        raise AnalysisInputError("物料编码和库存组织不能为空且不能超过 200 个字符。")
    result = store.fetch(
        """
        SELECT "是否超期", "库龄",
               SUM("含税总价"), SUM("数量"), MAX("超期天数"), COUNT(*)
        FROM inventory_snapshots
        WHERE "快照日期" = (SELECT MAX("快照日期") FROM inventory_snapshots)
          AND "物料编码" = ? AND "库存组织名称" = ?
          AND COALESCE("是否超期", '') IN ('Y', '是', '1')
        GROUP BY "是否超期", "库龄"
        ORDER BY MAX("超期天数") DESC, SUM("含税总价") DESC
        """,
        [material, org],
    )
    period = _period(_first_row(store.fetch('SELECT MAX("快照日期") FROM inventory_snapshots'))[0])
    rows = [list(row) for row in result.rows]
    return ToolResult(
        summary=f"物料 {material} 截至 {period} 返回 {len(rows)} 组超期库存记录。",
        columns=["是否超期", "库龄天数", "库存金额_元", "数量", "超期天数", "超期记录数"],
        rows=rows,
        sources=["inventory_snapshots"],
        period=period,
        metric_definitions=["只取全表最新季末且是否超期为Y、是或1的记录。"],
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


def _validate_evidence_query(
    investigation_profile: InvestigationProfile, query: EvidenceQuery
) -> SemanticCapability:
    capability = get_capability(query.dataset, query.grain)
    if capability is None or investigation_profile not in capability.investigation_profiles:
        raise AnalysisInputError(
            f"{investigation_profile} 案件不支持数据集 {query.dataset}/{query.grain}。"
        )
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
    total_rows = max(result.total_rows, len(rows))
    rows = rows[: query.limit]
    return result.model_copy(
        update={
            "columns": selected_columns,
            "rows": rows,
            "total_rows": total_rows,
            "returned_rows": len(rows),
            "is_truncated": total_rows > len(rows),
        }
    )


def _credit_query_result(
    result: ToolResult,
    customer_id: str,
    query: EvidenceQuery,
    capability: SemanticCapability,
) -> ToolResult:
    values = {str(row[0]): row[1] for row in result.rows}
    columns = ["客户编号", *(capability.output_metric_columns[item] for item in query.metrics)]
    row = [customer_id, *(values[column] for column in columns[1:])]
    return result.model_copy(
        update={
            "columns": columns,
            "rows": [row],
            "total_rows": 1,
            "returned_rows": 1,
            "is_truncated": False,
        }
    )


def query_business_evidence(
    store: DuckDBStore,
    investigation_profile: InvestigationProfile,
    subject_context: dict[str, JsonScalar],
    query: EvidenceQuery,
    *,
    business_type: BusinessType | None = None,
) -> ToolResult:
    """按单一语义注册表执行当前案件范围内的受控证据查询。"""

    capability = _validate_evidence_query(investigation_profile, query)
    key = (query.dataset, query.grain)
    if investigation_profile in ("RECEIVABLES", "PRE_TRANSACTION"):
        normalized_id = str(subject_context.get("customer_id", "")).strip().upper()
        if not re.fullmatch(r"C\d{3}", normalized_id):
            raise AnalysisInputError("案件缺少合法客户编号。")
    else:
        material = str(subject_context.get("material_code", "")).strip()
        org = str(subject_context.get("inventory_org", "")).strip()
        if not material or not org:
            raise AnalysisInputError("库存案件缺少物料编码或库存组织。")

    if key == ("proposal", "order"):
        if business_type is None:
            raise AnalysisInputError("事前案件缺少合法业务类型。")
        result = get_pre_transaction_proposal_evidence(subject_context, business_type)
    elif key == ("customer_profile", "business_type"):
        if business_type is None:
            raise AnalysisInputError("事前案件缺少合法业务类型。")
        result = get_customer_business_profile_evidence(store, normalized_id, business_type)
    elif key == ("receivables", "month"):
        result = get_customer_ar_history(
            store, normalized_id, months=_WINDOW_MONTHS[query.time_window]
        )
    elif key == ("receivables", "order"):
        result = get_current_receivable_details(
            store,
            normalized_id,
            limit=query.limit,
            sort_by=query.sort_by,
            sort_direction=query.sort_direction,
        )
    elif key == ("sales_payments", "month"):
        result = get_customer_flow_history(
            store,
            normalized_id,
            months=_WINDOW_MONTHS[query.time_window],
            business_type=business_type if investigation_profile == "PRE_TRANSACTION" else None,
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
    elif key == ("sales_returns", "customer"):
        result = get_customer_return_summary(store, normalized_id)
    elif key == ("sales_returns", "order"):
        result = get_customer_return_evidence(
            store,
            normalized_id,
            limit=query.limit,
            sort_by=query.sort_by,
            sort_direction=query.sort_direction,
        )
    elif key == ("payments", "customer"):
        result = get_customer_payment_risk_summary(store, normalized_id)
    elif key == ("payments", "order"):
        result = get_customer_payment_risk_evidence(
            store,
            normalized_id,
            limit=query.limit,
            sort_by=query.sort_by,
            sort_direction=query.sort_direction,
        )
    elif key == ("collections", "customer"):
        result = get_customer_collection_summary(store, normalized_id)
    elif key == ("collections", "order"):
        result = get_customer_collection_evidence(
            store,
            normalized_id,
            limit=query.limit,
            sort_by=query.sort_by,
            sort_direction=query.sort_direction,
        )
    elif key == ("inventory", "quarter"):
        result = get_material_inventory_history(store, material, org)
        result = result.model_copy(
            update={"rows": result.rows[: _WINDOW_QUARTERS[query.time_window]]}
        )
    elif key == ("inventory", "age_bucket"):
        result = get_material_inventory_age_profile(store, material, org)
    elif key == ("inventory", "inventory_record"):
        result = get_material_overdue_inventory_evidence(store, material, org)
    elif key == ("sales", "month"):
        result = get_material_sales_context(
            store, material, org, months=_WINDOW_MONTHS[query.time_window]
        )
    else:  # pragma: no cover - 所有注册项必须有固定执行器
        raise AnalysisInputError("当前查询组合尚未实现。")
    return _project_tool_result(result, query, capability=capability)


def discover_evidence_capabilities(
    store: DuckDBStore,
    investigation_profile: InvestigationProfile,
    subject_context: dict[str, JsonScalar],
    observation_date: str,
    *,
    business_type: BusinessType | None = None,
) -> BusinessDataCatalog:
    """用真实受控查询探测当前案件可用能力，不暴露 SQL 或物理字段。"""

    datasets = []
    for capability in capabilities_for(investigation_profile):
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
            result = query_business_evidence(
                store,
                investigation_profile,
                subject_context,
                query,
                business_type=business_type,
            )
            available = True
            total_rows = result.total_rows
            returned_rows = result.returned_rows
            is_truncated = result.is_truncated
            period = result.period
        except AnalysisInputError:
            available = False
            total_rows = 0
            returned_rows = 0
            is_truncated = False
            period = None
        datasets.append(
            DatasetCapability(
                dataset=capability.dataset,
                grain=capability.grain,
                description=capability.description,
                metrics=list(capability.metrics),
                time_windows=list(capability.time_windows),
                available=available,
                total_rows=total_rows,
                returned_rows=returned_rows,
                is_truncated=is_truncated,
                period=period,
                limitations=list(capability.limitations),
            )
        )
    if investigation_profile in ("RECEIVABLES", "PRE_TRANSACTION"):
        subject_scope = f"Customer {subject_context.get('customer_id', '')}"
        if investigation_profile == "PRE_TRANSACTION":
            subject_scope += f" / Business type {business_type or ''}"
    else:
        subject_scope = (
            f"Material {subject_context.get('material_code', '')} / "
            f"Inventory organization {subject_context.get('inventory_org', '')}"
        )
    return BusinessDataCatalog(
        investigation_profile=investigation_profile,
        subject_scope=subject_scope,
        observation_date=observation_date,
        datasets=datasets,
        global_rules=[
            "Each live probe reports total_rows, returned_rows, and whether the result is "
            "truncated.",
            "Every search and query is automatically restricted to the current case entity.",
            "Amounts, dates, ratios, and states must cite an evidence_id returned by get_evidence.",
            "Never submit SQL, file paths, regular expressions, or code.",
        ],
    )


def search_business_records(
    store: DuckDBStore,
    investigation_profile: InvestigationProfile,
    subject_context: dict[str, JsonScalar],
    search: BusinessRecordSearchQuery,
) -> ToolResult:
    """在案件主体的关联记录内按业务标识做参数化包含搜索。"""

    query_text = search.query.strip()
    rows: list[list[JsonScalar]]
    if investigation_profile in ("RECEIVABLES", "PRE_TRANSACTION"):
        customer_id = str(subject_context.get("customer_id", "")).strip().upper()
        if search.record_type == "customer":
            label = str(subject_context.get("customer_name", customer_id))
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
        material = str(subject_context.get("material_code", "")).strip()
        org = str(subject_context.get("inventory_org", "")).strip()
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

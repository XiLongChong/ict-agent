"""调查证据的单一类型化语义注册表。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ict_agent.models import (
    EvidenceDataset,
    EvidenceGrain,
    EvidenceMetric,
    EvidenceTimeWindow,
    InvestigationProfile,
)

_TIME_WINDOW_BREADTH: dict[EvidenceTimeWindow, int] = {
    "latest": 1,
    "last_3_months": 3,
    "last_6_months": 6,
    "last_12_months": 12,
    "all": 10_000,
}


def time_window_covers(
    broader: EvidenceTimeWindow | object, narrower: EvidenceTimeWindow | object
) -> bool:
    """判断一个受控时间窗口是否完整包含另一个窗口。"""

    if not isinstance(broader, str) or not isinstance(narrower, str):
        return broader == narrower
    if broader not in _TIME_WINDOW_BREADTH or narrower not in _TIME_WINDOW_BREADTH:
        return broader == narrower
    return _TIME_WINDOW_BREADTH[broader] >= _TIME_WINDOW_BREADTH[narrower]


@dataclass(frozen=True)
class SemanticCapability:
    """一个可执行的数据集、粒度、指标和窗口组合。"""

    dataset: EvidenceDataset
    grain: EvidenceGrain
    investigation_profiles: tuple[InvestigationProfile, ...]
    description: str
    metrics: tuple[EvidenceMetric, ...]
    time_windows: tuple[EvidenceTimeWindow, ...]
    dimension_columns: tuple[str, ...]
    source_metric_columns: Mapping[EvidenceMetric, str]
    output_metric_columns: Mapping[EvidenceMetric, str]
    limitations: tuple[str, ...]

    @property
    def key(self) -> tuple[EvidenceDataset, EvidenceGrain]:
        return self.dataset, self.grain


def _capability(
    dataset: EvidenceDataset,
    grain: EvidenceGrain,
    investigation_profile: InvestigationProfile | tuple[InvestigationProfile, ...],
    description: str,
    metrics: tuple[tuple[EvidenceMetric, str, str], ...],
    time_windows: tuple[EvidenceTimeWindow, ...],
    dimensions: tuple[str, ...],
    limitations: tuple[str, ...],
) -> SemanticCapability:
    return SemanticCapability(
        dataset=dataset,
        grain=grain,
        investigation_profiles=(investigation_profile,)
        if isinstance(investigation_profile, str)
        else investigation_profile,
        description=description,
        metrics=tuple(item[0] for item in metrics),
        time_windows=time_windows,
        dimension_columns=dimensions,
        source_metric_columns={item[0]: item[1] for item in metrics},
        output_metric_columns={item[0]: item[2] for item in metrics},
        limitations=limitations,
    )


SEMANTIC_CAPABILITIES: tuple[SemanticCapability, ...] = (
    _capability(
        "proposal",
        "order",
        "PRE_TRANSACTION",
        "Inspect the proposed amount, payment term, and expected margin for this simulated deal.",
        (
            ("proposed_amount", "拟交易金额_元", "拟交易金额_元"),
            ("proposed_term_days", "拟账期天数", "拟账期天数"),
            ("expected_margin_rate", "预期毛利率", "预期毛利率"),
        ),
        ("latest",),
        ("模拟交易编号", "客户编号", "业务类型", "场景"),
        ("This is simulated proposal input, not a completed sale.",),
    ),
    _capability(
        "customer_profile",
        "business_type",
        "PRE_TRANSACTION",
        (
            "Inspect the customer's historical order, payment-age, and margin baseline for this "
            "business type."
        ),
        (
            ("historical_order_count", "历史订单数", "历史订单数"),
            ("median_order_amount", "订单金额中位数_元", "订单金额中位数_元"),
            ("p90_order_amount", "订单金额P90_元", "订单金额P90_元"),
            ("median_payment_days", "回款账龄中位数_天", "回款账龄中位数_天"),
            ("median_margin_rate", "历史毛利率中位数", "历史毛利率中位数"),
        ),
        ("all",),
        ("客户编号", "业务类型"),
        (
            "Historical distribution shows deviation only; it is not a default probability or "
            "approval decision.",
        ),
    ),
    _capability(
        "receivables",
        "month",
        ("RECEIVABLES", "PRE_TRANSACTION"),
        "Inspect monthly receivable balances, overdue composition, and maximum overdue age.",
        (
            ("ar_amount", "应收余额_元", "应收金额_元"),
            ("overdue_amount", "超期应收_元", "超期应收_元"),
            ("overdue_30_amount", "30天以上超期_元", "30天以上超期_元"),
            ("overdue_60_amount", "60天以上超期_元", "60天以上超期_元"),
            ("overdue_rate", "超期率", "超期率"),
            ("max_overdue_days", "最大超期天数", "最大超期天数"),
        ),
        ("latest", "last_3_months", "last_6_months", "last_12_months", "all"),
        ("期间",),
        ("Month-end snapshots are point-in-time balances and must not be summed across periods.",),
    ),
    _capability(
        "receivables",
        "order",
        ("RECEIVABLES", "PRE_TRANSACTION"),
        (
            "Inspect receivables by contract, order, material, and promised payment date in the "
            "latest snapshot."
        ),
        (
            ("ar_amount", "应收金额_元", "应收金额_元"),
            ("overdue_amount", "超期应收_元", "超期应收_元"),
            ("overdue_30_amount", "30天以上超期_元", "30天以上超期_元"),
            ("overdue_60_amount", "60天以上超期_元", "60天以上超期_元"),
            ("max_overdue_days", "超期天数", "最大超期天数"),
        ),
        ("latest",),
        ("合同号", "销售订单号", "物料编码", "最终承诺还款日期", "是否展期"),
        ("Uses only the latest month-end snapshot; rows are risk-sorted and limited.",),
    ),
    _capability(
        "sales_payments",
        "month",
        ("RECEIVABLES", "PRE_TRANSACTION"),
        "Align sales, payments, estimated gross profit, and overdue interest by calendar month.",
        (
            ("sales_amount", "销售额_元", "销售额_元"),
            ("payment_amount", "回款额_元", "回款额_元"),
            ("gross_profit", "含税粗算毛利_元", "含税粗算毛利_元"),
            ("overdue_interest", "超期利息_元", "超期利息_元"),
            (
                "max_payment_overdue_days",
                "回款最大超期天数",
                "回款最大超期天数",
            ),
        ),
        ("last_3_months", "last_6_months", "last_12_months", "all"),
        ("月份",),
        ("Sales minus payments is not receivables and may only indicate operating direction.",),
    ),
    _capability(
        "extensions",
        "order",
        ("RECEIVABLES", "PRE_TRANSACTION"),
        (
            "Match current receivables to historical extensions by customer, contract, order, "
            "and material."
        ),
        (
            ("ar_amount", "当前应收_元", "应收金额_元"),
            ("overdue_amount", "当前超期_元", "超期应收_元"),
            ("matched_extension_actions", "匹配展期动作数", "匹配展期动作数"),
        ),
        ("all",),
        ("合同号", "销售订单号", "物料编码", "展期后最终承诺日", "最近展期记录日"),
        ("Extension data contains neither approver nor approval status.",),
    ),
    _capability(
        "credit",
        "customer",
        ("RECEIVABLES", "PRE_TRANSACTION"),
        "Inspect current credit, list status, financial profile, and credit-insurance master data.",
        (
            ("credit_limit", "授信额度", "授信额度"),
            ("list_status", "名单状态", "名单状态"),
            ("credit_rating", "失信分级", "失信分级"),
            ("net_assets", "净资产", "净资产"),
            ("net_profit", "净利润", "净利润"),
            ("credit_insurance", "信用保险", "信用保险"),
        ),
        ("latest",),
        ("客户编号",),
        ("Only current status is available; credit and list-status history is absent.",),
    ),
    _capability(
        "contracts",
        "contract",
        ("RECEIVABLES", "PRE_TRANSACTION"),
        (
            "Inspect contract, invoice, shipment, payment, and receivable data for open project "
            "exposure."
        ),
        (
            ("contract_amount", "签约金额_元", "签约金额_元"),
            ("invoiced_amount", "开票金额_元", "开票金额_元"),
            ("estimated_margin_rate", "实估毛利率", "实估毛利率"),
            ("actual_margin_rate", "实际净毛利率", "实际净毛利率"),
            ("margin_gap", "实估实际毛利率差", "实估实际毛利率差"),
            ("gross_profit", "实际净毛利_元", "实际净毛利_元"),
            ("contract_term_days", "合同文本账期_天", "合同文本账期_天"),
            ("actual_term_days", "实际账期_天", "实际账期_天"),
            ("term_overage_days", "账期超限_天", "账期超限_天"),
            ("shipped_amount", "出库金额_元", "出库金额_元"),
            ("payment_amount", "回款金额_元", "回款额_元"),
            ("ar_amount", "最新应收_元", "应收金额_元"),
            ("overdue_amount", "最新超期_元", "超期应收_元"),
        ),
        ("latest", "all"),
        ("合同号", "合同状态"),
        ("Project acceptance records are absent, so the contract loop is indirect evidence only.",),
    ),
    _capability(
        "sales_returns",
        "customer",
        "RECEIVABLES",
        "Inspect the complete customer-level sales and return totals used by the rule.",
        (
            ("gross_sales_amount", "销售净额_元", "销售净额_元"),
            ("return_amount", "退货金额_元", "退货金额_元"),
            ("return_ratio", "退货占比", "退货占比"),
        ),
        ("all",),
        ("客户编号",),
        ("This complete aggregate uses the same frozen net-sales denominator as the rule.",),
    ),
    _capability(
        "sales_returns",
        "order",
        "RECEIVABLES",
        "Inspect customer sales and returns by order across the fixed business snapshot.",
        (
            ("gross_sales_amount", "销售净额_元", "销售净额_元"),
            ("return_amount", "退货金额_元", "退货金额_元"),
            ("return_ratio", "退货占比", "退货占比"),
        ),
        ("all",),
        ("合同号", "销售订单号", "最早出库日", "最近出库日"),
        ("Returns preserve their original negative sales values before the ratio is calculated.",),
    ),
    _capability(
        "payments",
        "customer",
        "RECEIVABLES",
        "Inspect the complete customer-level payment risk totals used by the rules.",
        (
            ("payment_amount", "净回款额_元", "净回款额_元"),
            ("positive_payment_amount", "正向回款额_元", "正向回款额_元"),
            ("negative_payment_amount", "负回款金额_元", "负回款金额_元"),
            ("negative_payment_ratio", "负回款占比", "负回款占比"),
            ("over_365_payment_amount", "365天以上回款额_元", "365天以上回款额_元"),
            ("overdue_interest", "超期利息_元", "超期利息_元"),
            ("max_payment_overdue_days", "最大超期天数", "最大超期天数"),
            ("max_payment_age_days", "最大回款账龄_天", "最大回款账龄_天"),
        ),
        ("all",),
        ("客户编号",),
        ("This complete aggregate preserves offsets and the frozen payment-age definition.",),
    ),
    _capability(
        "payments",
        "order",
        "RECEIVABLES",
        "Inspect payments, offsets, overdue interest, and payment age by order.",
        (
            ("payment_amount", "净回款额_元", "净回款额_元"),
            ("positive_payment_amount", "正向回款额_元", "正向回款额_元"),
            ("negative_payment_amount", "负回款金额_元", "负回款金额_元"),
            ("negative_payment_ratio", "负回款占比", "负回款占比"),
            ("over_365_payment_amount", "365天以上回款额_元", "365天以上回款额_元"),
            ("overdue_interest", "超期利息_元", "超期利息_元"),
            ("max_payment_overdue_days", "最大超期天数", "最大超期天数"),
            ("max_payment_age_days", "最大回款账龄_天", "最大回款账龄_天"),
        ),
        ("all",),
        ("合同号", "销售订单号", "最早回款日", "最近回款日"),
        ("Negative payments are offsets, not evidence of missing bank settlement records.",),
    ),
    _capability(
        "collections",
        "customer",
        "RECEIVABLES",
        "Inspect the complete customer-level count, amount, and maximum age of unpaid orders.",
        (
            ("sales_amount", "正向销售额_元", "正向销售额_元"),
            ("payment_amount", "正向回款额_元", "正向回款额_元"),
            ("unpaid_amount", "未回款销售额_元", "未回款销售额_元"),
            ("max_unpaid_days", "最大未回款天数", "最大未回款天数"),
        ),
        ("all",),
        ("客户编号",),
        ("The rule treats any positive payment on an order as paid; this is not allocation.",),
    ),
    _capability(
        "collections",
        "order",
        "RECEIVABLES",
        "Reconcile positive sales orders with positive payments and age unpaid orders.",
        (
            ("sales_amount", "正向销售额_元", "正向销售额_元"),
            ("payment_amount", "正向回款额_元", "正向回款额_元"),
            ("unpaid_amount", "未回款销售额_元", "未回款销售额_元"),
            ("max_unpaid_days", "未回款天数", "未回款天数"),
        ),
        ("all",),
        ("合同号", "销售订单号", "最近出库日", "是否存在正向回款"),
        (
            "The frozen rule treats any positive payment on an order as paid; it is not invoice "
            "allocation.",
        ),
    ),
    _capability(
        "inventory",
        "quarter",
        "INVENTORY",
        (
            "Inspect quarterly inventory value, ageing composition, and weighted age for this "
            "material and organization."
        ),
        (
            ("inventory_amount", "库存金额_元", "库存金额_元"),
            ("fresh_inventory_amount", "60天内库存_元", "60天内库存_元"),
            ("stale_inventory_amount", "180天以上库存_元", "180天以上库存_元"),
            ("weighted_age_days", "加权库龄天数", "加权库龄天数"),
        ),
        ("latest", "last_3_months", "last_6_months", "last_12_months", "all"),
        ("期间",),
        ("Inventory is aggregated within each snapshot and must not be summed across periods.",),
    ),
    _capability(
        "inventory",
        "age_bucket",
        "INVENTORY",
        (
            "Inspect non-overlapping inventory age buckets and overdue-loan value at the latest "
            "quarter end."
        ),
        (
            ("inventory_amount", "库存金额_元", "库存金额_元"),
            ("inventory_quantity", "数量", "库存数量"),
            ("overdue_loan_amount", "借物超期金额_元", "借物超期金额_元"),
        ),
        ("latest",),
        ("库龄区间",),
        ("Age buckets use only the latest quarter-end snapshot.",),
    ),
    _capability(
        "inventory",
        "inventory_record",
        "INVENTORY",
        "Inspect overdue inventory records in the latest quarter-end snapshot.",
        (
            ("inventory_amount", "库存金额_元", "库存金额_元"),
            ("inventory_quantity", "数量", "库存数量"),
            ("max_inventory_overdue_days", "超期天数", "超期天数"),
            ("overdue_inventory_rows", "超期记录数", "超期记录数"),
        ),
        ("latest",),
        ("是否超期", "库龄天数"),
        ("Only rows marked overdue in the latest inventory snapshot are returned.",),
    ),
    _capability(
        "sales",
        "month",
        "INVENTORY",
        (
            "Inspect monthly sales, returns, quantity, and estimated gross profit for this "
            "material "
            "and organization."
        ),
        (
            ("sales_amount", "销售额_元", "销售额_元"),
            ("net_quantity", "净数量", "净销售数量"),
            ("return_amount", "退货金额_元", "退货金额_元"),
            ("gross_profit", "含税粗算毛利_元", "含税粗算毛利_元"),
            ("gross_margin", "粗算毛利率", "粗算毛利率"),
        ),
        ("last_3_months", "last_6_months", "last_12_months", "all"),
        ("月份",),
        (
            "Promotion and downstream-inventory data are absent, so sales changes cannot be "
            "attributed to a campaign.",
        ),
    ),
)

_CAPABILITY_BY_KEY = {item.key: item for item in SEMANTIC_CAPABILITIES}


def capabilities_for(investigation_profile: InvestigationProfile) -> tuple[SemanticCapability, ...]:
    """返回当前调查策略的全部受控语义能力。"""

    return tuple(
        item
        for item in SEMANTIC_CAPABILITIES
        if investigation_profile in item.investigation_profiles
    )


def get_capability(dataset: EvidenceDataset, grain: EvidenceGrain) -> SemanticCapability | None:
    """按稳定业务键查找能力，不暴露物理 SQL。"""

    return _CAPABILITY_BY_KEY.get((dataset, grain))

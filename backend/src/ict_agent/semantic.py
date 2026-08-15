"""调查证据的单一类型化语义注册表。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ict_agent.models import (
    CaseType,
    EvidenceDataset,
    EvidenceGrain,
    EvidenceMetric,
    EvidenceTimeWindow,
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
    case_types: tuple[CaseType, ...]
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
    case_type: CaseType | tuple[CaseType, ...],
    description: str,
    metrics: tuple[tuple[EvidenceMetric, str, str], ...],
    time_windows: tuple[EvidenceTimeWindow, ...],
    dimensions: tuple[str, ...],
    limitations: tuple[str, ...],
) -> SemanticCapability:
    return SemanticCapability(
        dataset=dataset,
        grain=grain,
        case_types=(case_type,) if isinstance(case_type, str) else case_type,
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
        ("ACCOUNTS_RECEIVABLE", "PRE_TRANSACTION"),
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
        ("ACCOUNTS_RECEIVABLE", "PRE_TRANSACTION"),
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
        ("ACCOUNTS_RECEIVABLE", "PRE_TRANSACTION"),
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
        ("ACCOUNTS_RECEIVABLE", "PRE_TRANSACTION"),
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
        ("ACCOUNTS_RECEIVABLE", "PRE_TRANSACTION"),
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
        ("ACCOUNTS_RECEIVABLE", "PRE_TRANSACTION"),
        (
            "Inspect contract, invoice, shipment, payment, and receivable data for open project "
            "exposure."
        ),
        (
            ("contract_amount", "签约金额_元", "签约金额_元"),
            ("invoiced_amount", "开票金额_元", "开票金额_元"),
            ("actual_margin_rate", "实际净毛利率", "实际净毛利率"),
            ("shipped_amount", "出库金额_元", "出库金额_元"),
            ("payment_amount", "回款金额_元", "回款额_元"),
            ("ar_amount", "最新应收_元", "应收金额_元"),
            ("overdue_amount", "最新超期_元", "超期应收_元"),
        ),
        ("latest",),
        ("合同号", "合同状态"),
        ("Project acceptance records are absent, so the contract loop is indirect evidence only.",),
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


def capabilities_for(case_type: CaseType) -> tuple[SemanticCapability, ...]:
    """返回当前案件类型的全部受控语义能力。"""

    return tuple(item for item in SEMANTIC_CAPABILITIES if case_type in item.case_types)


def get_capability(dataset: EvidenceDataset, grain: EvidenceGrain) -> SemanticCapability | None:
    """按稳定业务键查找能力，不暴露物理 SQL。"""

    return _CAPABILITY_BY_KEY.get((dataset, grain))

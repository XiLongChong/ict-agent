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
    case_type: CaseType
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
    case_type: CaseType,
    description: str,
    metrics: tuple[tuple[EvidenceMetric, str, str], ...],
    time_windows: tuple[EvidenceTimeWindow, ...],
    dimensions: tuple[str, ...],
    limitations: tuple[str, ...],
) -> SemanticCapability:
    return SemanticCapability(
        dataset=dataset,
        grain=grain,
        case_type=case_type,
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
        "receivables",
        "month",
        "ACCOUNTS_RECEIVABLE",
        "按月观察应收余额、超期结构和最大账龄。",
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
        ("月末快照是时点余额，不能跨期直接求和。",),
    ),
    _capability(
        "receivables",
        "order",
        "ACCOUNTS_RECEIVABLE",
        "查看最新快照中合同、订单、物料和承诺日级别的应收明细。",
        (
            ("ar_amount", "应收金额_元", "应收金额_元"),
            ("overdue_amount", "超期应收_元", "超期应收_元"),
            ("overdue_30_amount", "30天以上超期_元", "30天以上超期_元"),
            ("overdue_60_amount", "60天以上超期_元", "60天以上超期_元"),
            ("max_overdue_days", "超期天数", "最大超期天数"),
        ),
        ("latest",),
        ("合同号", "销售订单号", "物料编码", "最终承诺还款日期", "是否展期"),
        ("只取全表最新月末快照；结果按风险金额排序并受行数限制。",),
    ),
    _capability(
        "sales_payments",
        "month",
        "ACCOUNTS_RECEIVABLE",
        "按自然月对齐销售、回款、粗算毛利和超期利息。",
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
        ("销售减回款不等于应收，只能用于比较经营方向。",),
    ),
    _capability(
        "extensions",
        "order",
        "ACCOUNTS_RECEIVABLE",
        "将当前应收与历史展期动作按客户、合同、订单和物料精确匹配。",
        (
            ("ar_amount", "当前应收_元", "应收金额_元"),
            ("overdue_amount", "当前超期_元", "超期应收_元"),
            ("matched_extension_actions", "匹配展期动作数", "匹配展期动作数"),
        ),
        ("all",),
        ("合同号", "销售订单号", "物料编码", "展期后最终承诺日", "最近展期记录日"),
        ("展期表没有审批人和审批状态。",),
    ),
    _capability(
        "credit",
        "customer",
        "ACCOUNTS_RECEIVABLE",
        "查看客户当前授信、名单、财务概况和信用保险主数据。",
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
        ("只有当前状态，没有授信与名单历史。",),
    ),
    _capability(
        "contracts",
        "contract",
        "ACCOUNTS_RECEIVABLE",
        "查看当前未结应收所关联项目合同的签约、开票、出库和回款闭环。",
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
        ("没有项目验收记录，合同闭环只能作为间接证据。",),
    ),
    _capability(
        "inventory",
        "quarter",
        "INVENTORY",
        "按季末观察当前物料在库存组织中的金额、库龄结构和加权库龄。",
        (
            ("inventory_amount", "库存金额_元", "库存金额_元"),
            ("fresh_inventory_amount", "60天内库存_元", "60天内库存_元"),
            ("stale_inventory_amount", "180天以上库存_元", "180天以上库存_元"),
            ("weighted_age_days", "加权库龄天数", "加权库龄天数"),
        ),
        ("latest", "last_3_months", "last_6_months", "last_12_months", "all"),
        ("期间",),
        ("库存按单一期末快照聚合，不能跨期直接求和。",),
    ),
    _capability(
        "inventory",
        "age_bucket",
        "INVENTORY",
        "查看最新季末互不重叠的库存库龄分桶和借物超期金额。",
        (
            ("inventory_amount", "库存金额_元", "库存金额_元"),
            ("inventory_quantity", "数量", "库存数量"),
            ("overdue_loan_amount", "借物超期金额_元", "借物超期金额_元"),
        ),
        ("latest",),
        ("库龄区间",),
        ("库龄分桶只聚合全表最新季末。",),
    ),
    _capability(
        "sales",
        "month",
        "INVENTORY",
        "按月查看当前物料和库存组织的销售、退货、数量和粗算毛利。",
        (
            ("sales_amount", "销售额_元", "销售额_元"),
            ("net_quantity", "净数量", "净销售数量"),
            ("return_amount", "退货金额_元", "退货金额_元"),
            ("gross_profit", "含税粗算毛利_元", "含税粗算毛利_元"),
            ("gross_margin", "粗算毛利率", "粗算毛利率"),
        ),
        ("last_3_months", "last_6_months", "last_12_months", "all"),
        ("月份",),
        ("没有促销活动和下游库存，不能把销售变化归因到具体活动。",),
    ),
)

_CAPABILITY_BY_KEY = {item.key: item for item in SEMANTIC_CAPABILITIES}


def capabilities_for(case_type: CaseType) -> tuple[SemanticCapability, ...]:
    """返回当前案件类型的全部受控语义能力。"""

    return tuple(item for item in SEMANTIC_CAPABILITIES if item.case_type == case_type)


def get_capability(dataset: EvidenceDataset, grain: EvidenceGrain) -> SemanticCapability | None:
    """按稳定业务键查找能力，不暴露物理 SQL。"""

    return _CAPABILITY_BY_KEY.get((dataset, grain))

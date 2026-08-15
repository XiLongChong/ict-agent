"""调查策略与规则信号对应的最低证据要求。"""

from __future__ import annotations

from dataclasses import dataclass

from ict_agent.models import (
    EvidenceDataset,
    EvidenceGrain,
    EvidenceMetric,
    EvidenceRequirementView,
    EvidenceTimeWindow,
    InvestigationProfile,
)


@dataclass(frozen=True)
class EvidenceRequirement:
    dataset: EvidenceDataset
    grain: EvidenceGrain
    metrics: frozenset[EvidenceMetric]
    minimum_time_window: EvidenceTimeWindow
    require_complete_result: bool
    reason: str

    def to_view(self) -> EvidenceRequirementView:
        return EvidenceRequirementView(
            dataset=self.dataset,
            grain=self.grain,
            metrics=sorted(self.metrics),
            minimum_time_window=self.minimum_time_window,
            require_complete_result=self.require_complete_result,
            reason=self.reason,
        )


def _requirement(
    dataset: EvidenceDataset,
    grain: EvidenceGrain,
    metrics: tuple[EvidenceMetric, ...],
    minimum_time_window: EvidenceTimeWindow,
    reason: str,
    *,
    require_complete_result: bool = True,
) -> EvidenceRequirement:
    return EvidenceRequirement(
        dataset=dataset,
        grain=grain,
        metrics=frozenset(metrics),
        minimum_time_window=minimum_time_window,
        require_complete_result=require_complete_result,
        reason=reason,
    )


_BASE_REQUIREMENTS: dict[InvestigationProfile, tuple[EvidenceRequirement, ...]] = {
    "RECEIVABLES": (
        _requirement(
            "receivables",
            "month",
            (
                "ar_amount",
                "overdue_amount",
                "overdue_30_amount",
                "overdue_60_amount",
                "overdue_rate",
                "max_overdue_days",
            ),
            "last_12_months",
            "应收案件必须核对一年内的余额、超期结构和账龄变化。",
        ),
        _requirement(
            "receivables",
            "order",
            ("ar_amount", "overdue_amount", "overdue_60_amount", "max_overdue_days"),
            "latest",
            "应收案件必须下钻最新订单级敞口；明细可能截断但必须披露。",
            require_complete_result=False,
        ),
        _requirement(
            "sales_payments",
            "month",
            ("sales_amount", "payment_amount", "gross_profit", "overdue_interest"),
            "last_12_months",
            "应收案件必须对齐一年内销售、回款和经营方向。",
        ),
    ),
    "INVENTORY": (
        _requirement(
            "inventory",
            "quarter",
            (
                "inventory_amount",
                "fresh_inventory_amount",
                "stale_inventory_amount",
                "weighted_age_days",
            ),
            "all",
            "库存案件必须覆盖全部八期季末库存趋势。",
        ),
        _requirement(
            "inventory",
            "age_bucket",
            ("inventory_amount", "inventory_quantity", "overdue_loan_amount"),
            "latest",
            "库存案件必须核对最新季末库龄结构。",
        ),
        _requirement(
            "sales",
            "month",
            ("sales_amount", "net_quantity", "return_amount", "gross_profit"),
            "last_12_months",
            "库存案件必须核对一年内同物料同组织的销售消化情况。",
        ),
    ),
    "PRE_TRANSACTION": (
        _requirement(
            "proposal",
            "order",
            ("proposed_amount", "proposed_term_days", "expected_margin_rate"),
            "latest",
            "事前交易必须核对本次模拟订单。",
        ),
        _requirement(
            "customer_profile",
            "business_type",
            (
                "historical_order_count",
                "median_order_amount",
                "p90_order_amount",
                "median_payment_days",
                "median_margin_rate",
            ),
            "all",
            "事前交易必须使用客户同业务类型的完整历史基线。",
        ),
        _requirement(
            "receivables",
            "month",
            ("ar_amount", "overdue_amount", "overdue_rate", "max_overdue_days"),
            "last_12_months",
            "事前交易必须核对客户一年内应收风险。",
        ),
        _requirement(
            "sales_payments",
            "month",
            ("sales_amount", "payment_amount", "gross_profit", "overdue_interest"),
            "last_12_months",
            "事前交易必须核对同业务类型一年内销售回款。",
        ),
        _requirement(
            "credit",
            "customer",
            ("credit_limit", "list_status", "credit_rating", "credit_insurance"),
            "latest",
            "事前交易必须核对当前授信和名单状态。",
        ),
    ),
}


_SIGNAL_REQUIREMENTS: dict[str, tuple[EvidenceRequirement, ...]] = {
    "AR_OPERATING_DEEP_OVERDUE": (
        _requirement(
            "extensions",
            "order",
            ("ar_amount", "overdue_amount", "matched_extension_actions"),
            "all",
            "深度超期必须核对当前应收与历史展期的精确匹配。",
            require_complete_result=False,
        ),
        _requirement(
            "credit", "customer", ("credit_limit", "list_status"), "latest", "核对当前授信。"
        ),
    ),
    "AR_OPERATING_EXPOSURE_BUILDUP": (
        _requirement(
            "contracts",
            "contract",
            ("contract_amount", "shipped_amount", "payment_amount", "ar_amount"),
            "all",
            "敞口积累必须核对正式合同闭环。",
            require_complete_result=False,
        ),
        _requirement(
            "credit", "customer", ("credit_limit", "list_status"), "latest", "核对当前授信。"
        ),
    ),
    "AR_BLACKLIST_EXPOSURE": (
        _requirement(
            "credit", "customer", ("credit_limit", "list_status"), "latest", "核对名单与授信。"
        ),
    ),
    "AR_OVERDUE_RATE_HIGH": (
        _requirement(
            "receivables",
            "month",
            ("ar_amount", "overdue_amount", "overdue_rate", "max_overdue_days"),
            "last_12_months",
            "高超期率必须核对一年内超期比例和账龄趋势。",
        ),
    ),
    "AR_OVER_CREDIT_LIMIT": (
        _requirement(
            "credit", "customer", ("credit_limit", "list_status"), "latest", "核对授信额度。"
        ),
    ),
    "AR_NO_CREDIT_WITH_EXPOSURE": (
        _requirement(
            "credit", "customer", ("credit_limit", "list_status"), "latest", "核对授信缺口。"
        ),
    ),
    "CREDIT_EXPOSURE_DECLINE": (
        _requirement(
            "credit",
            "customer",
            ("credit_limit", "list_status", "net_assets", "net_profit"),
            "latest",
            "核对授信敞口与财务背景。",
        ),
    ),
    "AR_UNPAID_AGING": (
        _requirement(
            "collections",
            "customer",
            ("sales_amount", "payment_amount", "unpaid_amount", "max_unpaid_days"),
            "all",
            "长期未回款必须核对订单级销售与正向回款。",
        ),
    ),
    "AR_EXTENSION_ABUSE": (
        _requirement(
            "extensions",
            "order",
            ("ar_amount", "overdue_amount", "matched_extension_actions"),
            "all",
            "多次展期必须核对精确匹配的展期动作。",
            require_complete_result=False,
        ),
    ),
    "AR_PENALTY_INTEREST_HIGH": (
        _requirement(
            "payments",
            "customer",
            ("payment_amount", "overdue_interest", "max_payment_overdue_days"),
            "all",
            "高额罚息必须核对订单级回款和罚息。",
        ),
    ),
    "SLS_RETURN_ABNORMAL": (
        _requirement(
            "sales_returns",
            "customer",
            ("gross_sales_amount", "return_amount", "return_ratio"),
            "all",
            "异常退货必须核对订单级销售与退货净额。",
        ),
    ),
    "PAY_OFFSET_ABNORMAL": (
        _requirement(
            "payments",
            "customer",
            ("payment_amount", "negative_payment_amount", "negative_payment_ratio"),
            "all",
            "负回款必须核对订单级冲销金额。",
        ),
    ),
    "PAY_AGING_OVER_365": (
        _requirement(
            "payments",
            "customer",
            ("payment_amount", "over_365_payment_amount", "max_payment_age_days"),
            "all",
            "超长账龄必须核对365天以上回款。",
        ),
    ),
    "CON_NEGATIVE_MARGIN": (
        _requirement(
            "contracts",
            "contract",
            ("contract_amount", "actual_margin_rate", "gross_profit"),
            "all",
            "负毛利信号必须核对客户关联合同的实际毛利。",
            require_complete_result=False,
        ),
    ),
    "CON_MARGIN_OPTIMISTIC": (
        _requirement(
            "contracts",
            "contract",
            ("contract_amount", "estimated_margin_rate", "actual_margin_rate", "margin_gap"),
            "all",
            "毛利高估必须核对金额加权的实估与实际毛利差。",
            require_complete_result=False,
        ),
    ),
    "CON_TERM_OVERAGE": (
        _requirement(
            "contracts",
            "contract",
            ("contract_amount", "contract_term_days", "actual_term_days", "term_overage_days"),
            "all",
            "账期超限必须核对合同约定与实际账期。",
            require_complete_result=False,
        ),
    ),
    "INV_OVERDUE_STOCK": (
        _requirement(
            "inventory",
            "inventory_record",
            (
                "inventory_amount",
                "inventory_quantity",
                "max_inventory_overdue_days",
                "overdue_inventory_rows",
            ),
            "latest",
            "库存超期必须核对最新季末超期记录。",
            require_complete_result=False,
        ),
    ),
    "INV_MATERIAL_BUILDUP": (
        _requirement(
            "inventory",
            "quarter",
            ("inventory_amount", "fresh_inventory_amount", "stale_inventory_amount"),
            "all",
            "库存增长必须核对全部季度趋势。",
        ),
    ),
    "INV_STALE_NO_SALES": (
        _requirement(
            "inventory",
            "age_bucket",
            ("inventory_amount", "inventory_quantity"),
            "latest",
            "高库龄必须核对最新库龄结构。",
        ),
        _requirement(
            "sales",
            "month",
            ("sales_amount", "net_quantity"),
            "last_12_months",
            "无销售必须核对同物料同组织销售历史。",
        ),
    ),
    "INV_BUILDUP_SALES_SLOWDOWN": (
        _requirement("inventory", "quarter", ("inventory_amount",), "all", "核对库存增长趋势。"),
        _requirement(
            "sales",
            "month",
            ("sales_amount", "net_quantity"),
            "last_12_months",
            "核对销售放缓趋势。",
        ),
    ),
    "INV_ZERO_SALES_STOCK": (
        _requirement("inventory", "quarter", ("inventory_amount",), "all", "核对库存敞口。"),
        _requirement(
            "sales",
            "month",
            ("sales_amount", "net_quantity"),
            "last_3_months",
            "零销售信号必须至少核对最近三个月。",
        ),
    ),
    "INV_STALE_RATIO_HIGH": (
        _requirement(
            "inventory",
            "age_bucket",
            ("inventory_amount", "inventory_quantity"),
            "latest",
            "呆滞占比必须核对最新库龄分桶。",
        ),
    ),
    "INV_VERY_OLD_STOCK": (
        _requirement(
            "inventory",
            "age_bucket",
            ("inventory_amount", "inventory_quantity"),
            "latest",
            "超长库龄必须核对365天以上分桶。",
        ),
    ),
    "PRE_TRANSACTION_REVIEW": (),
}


_WINDOW_ORDER: dict[EvidenceTimeWindow, int] = {
    "latest": 1,
    "last_3_months": 3,
    "last_6_months": 6,
    "last_12_months": 12,
    "all": 10_000,
}


def requirements_for(
    investigation_profile: InvestigationProfile, signal_codes: set[str]
) -> tuple[EvidenceRequirement, ...]:
    """返回基础策略与全部命中信号要求的并集。"""

    merged: dict[tuple[EvidenceDataset, EvidenceGrain], EvidenceRequirement] = {}
    candidates = list(_BASE_REQUIREMENTS[investigation_profile])
    for signal_code in sorted(signal_codes):
        candidates.extend(_SIGNAL_REQUIREMENTS.get(signal_code, ()))
    for requirement in candidates:
        key = (requirement.dataset, requirement.grain)
        previous = merged.get(key)
        if previous is None:
            merged[key] = requirement
            continue
        minimum_time_window = max(
            (previous.minimum_time_window, requirement.minimum_time_window),
            key=_WINDOW_ORDER.__getitem__,
        )
        reasons = list(dict.fromkeys((previous.reason, requirement.reason)))
        merged[key] = EvidenceRequirement(
            dataset=requirement.dataset,
            grain=requirement.grain,
            metrics=previous.metrics | requirement.metrics,
            minimum_time_window=minimum_time_window,
            require_complete_result=(
                previous.require_complete_result or requirement.require_complete_result
            ),
            reason=" ".join(reasons),
        )
    return tuple(merged[key] for key in sorted(merged))


def supported_signal_codes() -> frozenset[str]:
    """返回已经显式定义证据策略的规则或入口信号。"""

    return frozenset(_SIGNAL_REQUIREMENTS)

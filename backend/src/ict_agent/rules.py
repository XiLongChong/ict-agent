"""版本化确定性规则，只负责计算并产出原始规则命中。"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from ict_agent.data import DatabaseScalar, DuckDBStore
from ict_agent.pretransaction import SimulatedOrder
from ict_agent.rule_models import RuleHit, RuleHitBatch, RuleSubject
from ict_agent.tools import (
    get_aging_payment_features,
    get_customer_return_features,
    get_extension_rule_features,
    get_inventory_overdue_stock_features,
    get_inventory_rule_features,
    get_inventory_stale_ratio_features,
    get_inventory_very_old_features,
    get_inventory_zero_sales_features,
    get_margin_optimistic_features,
    get_negative_margin_features,
    get_negative_payment_features,
    get_penalty_interest_features,
    get_receivable_rule_features,
    get_term_overage_features,
    get_unpaid_sales_features,
)

RULE_SET_VERSION = "2026.08-v2"
RULE_VERSION = "2.0.0"
THRESHOLD_SOURCE = "赛事模拟数据24个月/8季度回溯后冻结的经营中客户与库存早期预警阈值"


@dataclass(frozen=True)
class RuleThresholds:
    """首期候选案件阈值；测试可注入更小的固定值。"""

    deep_overdue_amount: float = 1_000_000
    deep_overdue_days: int = 90
    overdue_growth_amount: float = 1_000_000
    stale_inventory_amount: float = 500_000
    stale_inventory_rate: float = 0.30
    inventory_buildup_amount: float = 10_000_000
    inventory_buildup_rate: float = 0.50
    inventory_slowdown_amount: float = 3_000_000
    inventory_sales_decline_rate: float = 0.50
    # --- v2 新增阈值（2026.08-v2 候选） ---
    overdue_rate_threshold: float = 0.70  # A1 高超期率
    unpaid_aging_days: int = 90  # A2 长期销售未回款账龄
    unpaid_amount: float = 1_000_000  # A2 客户级无回款金额下限
    zero_sales_inventory_amount: float = 5_000_000  # B1 高库存零销售金额下限(500万，~21件)
    very_old_inventory_days: int = 365  # B3 超长库龄天数
    very_old_inventory_amount: float = 500_000  # B3 超长库龄金额下限
    extension_count_min: int = 5  # A5 多次展期次数下限
    penalty_interest_amount: float = 500_000  # A6 高额罚息金额下限
    stale_ratio_threshold: float = 0.50  # B2 呆滞占比下限
    stale_ratio_amount: float = 500_000  # B2 呆滞金额下限
    borrow_overdue_days: int = 60  # B4 超期库存天数下限（重做后 60 天）
    overdue_stock_amount: float = 500_000  # B4 超期库存金额下限
    # --- v2 第三批新增阈值 ---
    return_ratio_threshold: float = 0.15  # C1 异常退货占比下限
    return_amount: float = 1_000_000  # C1 退货金额下限
    negative_payment_amount: float = 1_000_000  # C3 负回款金额下限
    negative_payment_ratio: float = 0.15  # C3 负回款占比下限
    aging_overdue_amount: float = 500_000  # C4 超长账龄回款金额下限
    negative_margin_loss: float = 500_000  # D1 负毛利亏损金额下限
    margin_gap: float = 0.05  # D2 实估-实际毛利差值下限(pt)
    margin_actual_max: float = 0.02  # D2 实际毛利率上限
    term_overage_days: int = 120  # D3 账期超期天数下限
    term_overage_amount: float = 1_000_000  # D3 账期超期金额下限
    credit_zero_recent_sales_ar: float = 1_000_000  # E2 无授信有应收金额下限
    no_credit_min_ar: float = 1_000_000  # A4 无授信有敞口的应收金额下限


def _value(row: tuple[DatabaseScalar, ...], index: int) -> DatabaseScalar:
    return row[index]


def _text(row: tuple[DatabaseScalar, ...], index: int) -> str:
    value = _value(row, index)
    return "" if value is None else str(value)


def _number_value(row: tuple[DatabaseScalar, ...], index: int) -> float:
    value = _value(row, index)
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    return float(value)


def _short_id(prefix: str, value: str) -> str:
    return f"{prefix}_{sha256(value.encode('utf-8')).hexdigest()[:20]}"


def _latest_ar_date(store: DuckDBStore) -> str:
    latest = store.fetch('SELECT MAX("快照时间") FROM ar_snapshots').rows[0][0]
    return str(latest).split("T", maxsplit=1)[0] if latest is not None else ""


_ENTITY_KEY = "_entity"


def _entity_meta(
    *,
    investigation_profile: str,
    subject_type: str,
    subject_id: str,
    subject_label: str,
    subject_context: dict[str, object],
    observation_date: str,
    ar_balance: float | None = None,
    inv_amount: float | None = None,
) -> dict[str, object]:
    """构造规则命中携带的主体元数据，供准入和组装阶段使用。"""

    return {
        "investigation_profile": investigation_profile,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "subject_label": subject_label,
        "subject_context": subject_context,
        "observation_date": observation_date,
        "ar_balance": ar_balance,
        "inv_amount": inv_amount,
    }


def _money(value: float) -> str:
    if abs(value) >= 100_000_000:
        return f"{value / 100_000_000:.2f} 亿元"
    if abs(value) >= 10_000:
        return f"{value / 10_000:.2f} 万元"
    return f"{value:.2f} 元"


def _hit(
    *,
    admission_key: str,
    rule_id: str,
    rule_name: str,
    severity: str,
    exposure_amount: float,
    reason: str,
    metrics: dict[str, object],
    sources: list[str],
    period: str,
) -> RuleHit:
    clean_metrics = dict(metrics)
    entity = clean_metrics.pop(_ENTITY_KEY, None)
    if not isinstance(entity, dict):
        raise ValueError(f"规则命中缺少主体元数据：{rule_id}")
    subject_context = entity.get("subject_context")
    return RuleHit(
        rule_hit_id=_short_id("hit", f"{admission_key}|{rule_id}|{RULE_VERSION}"),
        subject=RuleSubject(
            admission_key=admission_key,
            investigation_profile=str(entity["investigation_profile"]),
            subject_type=str(entity["subject_type"]),
            subject_id=str(entity["subject_id"]),
            subject_label=str(entity["subject_label"]),
            subject_context=(dict(subject_context) if isinstance(subject_context, dict) else {}),
            observation_date=str(entity["observation_date"]),
            exposure_amount=(
                float(entity["ar_balance"])
                if isinstance(entity.get("ar_balance"), (int, float))
                else (
                    float(entity["inv_amount"])
                    if isinstance(entity.get("inv_amount"), (int, float))
                    else None
                )
            ),
        ),
        rule_id=rule_id,
        rule_name=rule_name,
        rule_version=RULE_VERSION,
        severity=severity,
        exposure_amount=exposure_amount,
        reason=reason,
        metrics=clean_metrics,
        threshold_source=THRESHOLD_SOURCE,
        sources=tuple(sources),
        period=period,
    )


def _receivable_hits(
    store: DuckDBStore,
    thresholds: RuleThresholds,
) -> tuple[list[RuleHit], str]:
    result = get_receivable_rule_features(store)
    all_hits: list[RuleHit] = []
    observation_date = ""

    for row in result.rows:
        observation_date = _text(row, 0).split("T", maxsplit=1)[0]
        customer_id = _text(row, 1)
        customer_name = _text(row, 2)
        ar_amount = _number_value(row, 3)
        overdue_amount = _number_value(row, 4)
        overdue_60_amount = _number_value(row, 5)
        overdue_60_rate = _number_value(row, 6)
        max_overdue_days = int(_number_value(row, 7))
        baseline_overdue_amount = _number_value(row, 8)
        overdue_growth_3m = _number_value(row, 9)
        sales_3m = _number_value(row, 10)
        payments_3m = _number_value(row, 11)
        list_status = int(_number_value(row, 12))
        credit_limit = _number_value(row, 13)
        is_operating = sales_3m != 0 or payments_3m != 0
        admission_key = f"AR|{customer_id}"
        hits: list[RuleHit] = []

        common_metrics: dict[str, object] = {
            "ar_amount": ar_amount,
            "overdue_amount": overdue_amount,
            "overdue_60_amount": overdue_60_amount,
            "overdue_60_rate": overdue_60_rate,
            "max_overdue_days": max_overdue_days,
            "baseline_overdue_amount": baseline_overdue_amount,
            "overdue_growth_3m": overdue_growth_3m,
            "sales_3m": sales_3m,
            "payments_3m": payments_3m,
            "list_status": list_status,
            "credit_limit": credit_limit,
            _ENTITY_KEY: _entity_meta(
                investigation_profile="RECEIVABLES",
                subject_type="CUSTOMER",
                subject_id=customer_id,
                subject_label=f"{customer_id} {customer_name}".strip(),
                subject_context={"customer_id": customer_id, "customer_name": customer_name},
                observation_date=observation_date,
                ar_balance=ar_amount,
            ),
        }

        if (
            list_status != 2
            and is_operating
            and overdue_60_amount >= thresholds.deep_overdue_amount
            and max_overdue_days >= thresholds.deep_overdue_days
        ):
            hits.append(
                _hit(
                    admission_key=admission_key,
                    rule_id="AR_OPERATING_DEEP_OVERDUE",
                    rule_name="经营中客户大额深度超期",
                    severity="HIGH",
                    exposure_amount=overdue_60_amount,
                    reason=(
                        f"60天以上超期 {_money(overdue_60_amount)}，最大超期 "
                        f"{max_overdue_days} 天，且近三个月仍有销售或回款，"
                        "需要调查风险是否继续恶化。"
                    ),
                    metrics=common_metrics,
                    sources=["ar_snapshots"],
                    period=observation_date,
                )
            )

        if (
            list_status != 2
            and overdue_amount >= thresholds.overdue_growth_amount
            and overdue_growth_3m >= thresholds.overdue_growth_amount
            and sales_3m > 0
            and sales_3m > payments_3m
        ):
            hits.append(
                _hit(
                    admission_key=admission_key,
                    rule_id="AR_OPERATING_EXPOSURE_BUILDUP",
                    rule_name="经营中客户应收敞口加速积累",
                    severity="HIGH",
                    exposure_amount=overdue_amount,
                    reason=(
                        f"近三个月超期增加 {_money(overdue_growth_3m)}，且新增销售 "
                        f"{_money(sales_3m)} 高于回款 {_money(payments_3m)}；客户尚未进入黑名单。"
                    ),
                    metrics=common_metrics,
                    sources=["ar_snapshots", "sales", "payments"],
                    period=observation_date,
                )
            )

        if list_status == 2 and ar_amount > 0:
            hits.append(
                _hit(
                    admission_key=admission_key,
                    rule_id="AR_BLACKLIST_EXPOSURE",
                    rule_name="黑名单客户仍有应收敞口",
                    severity="CRITICAL",
                    exposure_amount=ar_amount,
                    reason=f"客户当前为黑名单，仍有应收 {_money(ar_amount)}。",
                    metrics=common_metrics,
                    sources=["customer_credit", "ar_snapshots"],
                    period=observation_date,
                )
            )

        # --- 2026.08-v2 新增 ---
        overdue_rate = overdue_amount / ar_amount if ar_amount > 0 else 0.0
        # A1 高超期率
        if (
            overdue_rate >= thresholds.overdue_rate_threshold
            and overdue_amount >= thresholds.deep_overdue_amount
        ):
            hits.append(
                _hit(
                    admission_key=admission_key,
                    rule_id="AR_OVERDUE_RATE_HIGH",
                    rule_name="高超期率客户",
                    severity="HIGH",
                    exposure_amount=overdue_amount,
                    reason=(
                        f"超期率 {overdue_rate:.0%}，超期应收 {_money(overdue_amount)}，"
                        "应收几乎全部逾期，需调查是否存量恶化或新增持续超期。"
                    ),
                    metrics=common_metrics,
                    sources=["ar_snapshots"],
                    period=observation_date,
                )
            )
        # A3 应收超授信（授信额度单位为万元）
        credit_limit_yuan = credit_limit * 10000
        if credit_limit > 0 and ar_amount > credit_limit_yuan:
            hits.append(
                _hit(
                    admission_key=admission_key,
                    rule_id="AR_OVER_CREDIT_LIMIT",
                    rule_name="应收超授信额度",
                    severity="HIGH",
                    exposure_amount=ar_amount - credit_limit_yuan,
                    reason=(
                        f"应收 {_money(ar_amount)} 超过授信额度 "
                        f"{_money(credit_limit_yuan)}，超出 "
                        f"{_money(ar_amount - credit_limit_yuan)}。"
                    ),
                    metrics=common_metrics,
                    sources=["customer_credit", "ar_snapshots"],
                    period=observation_date,
                )
            )
        # A4 无授信仍有应收敞口（含金额材料性下限）
        if credit_limit == 0 and ar_amount >= thresholds.no_credit_min_ar:
            hits.append(
                _hit(
                    admission_key=admission_key,
                    rule_id="AR_NO_CREDIT_WITH_EXPOSURE",
                    rule_name="无授信仍有应收敞口",
                    severity="MEDIUM",
                    exposure_amount=ar_amount,
                    reason=f"客户无授信额度，仍有应收 {_money(ar_amount)}。",
                    metrics=common_metrics,
                    sources=["customer_credit", "ar_snapshots"],
                    period=observation_date,
                )
            )
            # E2 授信敞口失衡：A4 的高优先级子集（应收>=下限 且 近3月有销售）
            if ar_amount >= thresholds.credit_zero_recent_sales_ar and sales_3m > 0:
                hits.append(
                    _hit(
                        admission_key=admission_key,
                        rule_id="CREDIT_EXPOSURE_DECLINE",
                        rule_name="授信敞口失衡",
                        severity="HIGH",
                        exposure_amount=ar_amount,
                        reason=(
                            f"客户无授信额度，仍有应收 {_money(ar_amount)}"
                            f"且近 3 个月仍有销售 {_money(sales_3m)}。"
                        ),
                        metrics=common_metrics,
                        sources=["customer_credit", "ar_snapshots", "sales"],
                        period=observation_date,
                    )
                )
        if not hits:
            continue
        all_hits.extend(hits)
    return all_hits, observation_date


def _inventory_hits(
    store: DuckDBStore,
    thresholds: RuleThresholds,
) -> tuple[list[RuleHit], str]:
    result = get_inventory_rule_features(store)
    all_hits: list[RuleHit] = []
    observation_date = ""

    for row in result.rows:
        observation_date = _text(row, 0).split("T", maxsplit=1)[0]
        material_code = _text(row, 1)
        inventory_org = _text(row, 2)
        inventory_amount = _number_value(row, 3)
        previous_inventory_amount = _number_value(row, 4)
        inventory_growth = _number_value(row, 5)
        inventory_growth_rate = None if _value(row, 6) is None else _number_value(row, 6)
        stale_amount = _number_value(row, 7)
        stale_rate = _number_value(row, 8)
        fresh_amount = _number_value(row, 9)
        sales_3m = _number_value(row, 10)
        previous_sales_3m = _number_value(row, 11)
        subject_id = f"{material_code}|{inventory_org}"
        admission_key = f"INV|{subject_id}"
        hits: list[RuleHit] = []
        is_material_buildup = inventory_growth >= thresholds.inventory_buildup_amount and (
            previous_inventory_amount == 0
            or (
                inventory_growth_rate is not None
                and inventory_growth_rate >= thresholds.inventory_buildup_rate
            )
        )
        is_smaller_buildup = inventory_growth >= thresholds.inventory_slowdown_amount and (
            previous_inventory_amount == 0
            or (
                inventory_growth_rate is not None
                and inventory_growth_rate >= thresholds.inventory_buildup_rate
            )
        )
        sales_declined = (
            previous_sales_3m > 0
            and sales_3m < previous_sales_3m * thresholds.inventory_sales_decline_rate
        )
        common_metrics: dict[str, object] = {
            "inventory_amount": inventory_amount,
            "previous_inventory_amount": previous_inventory_amount,
            "inventory_growth": inventory_growth,
            "inventory_growth_rate": inventory_growth_rate,
            "stale_amount": stale_amount,
            "stale_rate": stale_rate,
            "fresh_amount": fresh_amount,
            "sales_3m": sales_3m,
            "previous_sales_3m": previous_sales_3m,
            _ENTITY_KEY: _entity_meta(
                investigation_profile="INVENTORY",
                subject_type="MATERIAL_INVENTORY_ORG",
                subject_id=subject_id,
                subject_label=material_code,
                subject_context={
                    "material_code": material_code,
                    "inventory_org": inventory_org,
                },
                observation_date=observation_date,
                inv_amount=inventory_amount,
            ),
        }

        if is_material_buildup:
            hits.append(
                _hit(
                    admission_key=admission_key,
                    rule_id="INV_MATERIAL_BUILDUP",
                    rule_name="库存金额显著增加",
                    severity="MEDIUM",
                    exposure_amount=inventory_amount,
                    reason=(
                        f"本季库存较上季增加 {_money(inventory_growth)}，需要调查是正常补货"
                        "还是需求转弱。"
                    ),
                    metrics=common_metrics,
                    sources=["inventory_snapshots"],
                    period=observation_date,
                )
            )

        if (
            stale_amount >= thresholds.stale_inventory_amount
            and stale_rate >= thresholds.stale_inventory_rate
            and sales_3m <= 0
        ):
            hits.append(
                _hit(
                    admission_key=admission_key,
                    rule_id="INV_STALE_NO_SALES",
                    rule_name="高库龄库存且近期无销售",
                    severity="HIGH",
                    exposure_amount=stale_amount,
                    reason=(
                        f"180天以上库存 {_money(stale_amount)}，占当前库存 "
                        f"{stale_rate:.1%}，近三个月无销售。"
                    ),
                    metrics=common_metrics,
                    sources=["inventory_snapshots", "sales"],
                    period=observation_date,
                )
            )

        if is_smaller_buildup and sales_declined:
            hits.append(
                _hit(
                    admission_key=admission_key,
                    rule_id="INV_BUILDUP_SALES_SLOWDOWN",
                    rule_name="库存增加且销售显著下降",
                    severity="HIGH",
                    exposure_amount=inventory_amount,
                    reason=(
                        f"库存增加 {_money(inventory_growth)}，近三个月销售由 "
                        f"{_money(previous_sales_3m)} 降至 {_money(sales_3m)}。"
                    ),
                    metrics=common_metrics,
                    sources=["inventory_snapshots", "sales"],
                    period=observation_date,
                )
            )

        if not hits:
            continue
        all_hits.extend(hits)
    return all_hits, observation_date


def _unpaid_sales_hits(
    store: DuckDBStore,
    thresholds: RuleThresholds,
) -> tuple[list[RuleHit], str]:
    """A2 长期销售未回款：出库超过 90 天仍无正回款的销售订单，按客户聚合。"""

    result = get_unpaid_sales_features(store)
    all_hits: list[RuleHit] = []
    observation_date = ""

    for row in result.rows:
        customer_id = _text(row, 0)
        customer_name = _text(row, 1)
        unpaid_order_count = int(_number_value(row, 2))
        unpaid_amount = _number_value(row, 3)
        unpaid_ge90_amount = _number_value(row, 4)
        max_unpaid_days = int(_number_value(row, 5))
        if observation_date == "":
            latest = store.fetch('SELECT MAX("快照时间") FROM ar_snapshots').rows[0][0]
            observation_date = _text((latest,), 0).split("T", maxsplit=1)[0]
        if unpaid_ge90_amount < thresholds.unpaid_amount:
            continue
        admission_key = f"AR|{customer_id}"
        hit = _hit(
            admission_key=admission_key,
            rule_id="AR_UNPAID_AGING",
            rule_name="长期销售未回款",
            severity="HIGH",
            exposure_amount=unpaid_ge90_amount,
            reason=(
                f"有 {unpaid_order_count} 个销售订单出库超 90 天仍无正回款，"
                f"其中超 90 天未回款金额 {_money(unpaid_ge90_amount)}，"
                f"最大未回款账龄 {max_unpaid_days} 天。"
            ),
            metrics={
                "unpaid_order_count": unpaid_order_count,
                "unpaid_amount": unpaid_amount,
                "unpaid_ge90_amount": unpaid_ge90_amount,
                "max_unpaid_days": max_unpaid_days,
                _ENTITY_KEY: _entity_meta(
                    investigation_profile="RECEIVABLES",
                    subject_type="CUSTOMER",
                    subject_id=customer_id,
                    subject_label=f"{customer_id} {customer_name}".strip(),
                    subject_context={"customer_id": customer_id, "customer_name": customer_name},
                    observation_date=observation_date,
                    ar_balance=unpaid_ge90_amount,
                ),
            },
            sources=["sales", "payments"],
            period=observation_date,
        )
        all_hits.append(hit)
    return all_hits, observation_date


def _zero_sales_inventory_hits(
    store: DuckDBStore,
    thresholds: RuleThresholds,
) -> tuple[list[RuleHit], str]:
    """B1 高库存但近三个月零销售：广口径潜在呆滞。"""

    result = get_inventory_zero_sales_features(store)
    all_hits: list[RuleHit] = []
    observation_date = ""

    for row in result.rows:
        observation_date = _text(row, 0).split("T", maxsplit=1)[0]
        material_code = _text(row, 1)
        inventory_org = _text(row, 2)
        inventory_amount = _number_value(row, 3)
        sales_3m = _number_value(row, 4)
        if sales_3m > 0 or inventory_amount < thresholds.zero_sales_inventory_amount:
            continue
        subject_id = f"{material_code}|{inventory_org}"
        admission_key = f"INV|{subject_id}"
        hit = _hit(
            admission_key=admission_key,
            rule_id="INV_ZERO_SALES_STOCK",
            rule_name="高库存但近期零销售",
            severity="HIGH",
            exposure_amount=inventory_amount,
            reason=f"库存 {_money(inventory_amount)}，近三个月无正销售，可能为滞销。",
            metrics={
                "inventory_amount": inventory_amount,
                "sales_3m": sales_3m,
                _ENTITY_KEY: _entity_meta(
                    investigation_profile="INVENTORY",
                    subject_type="MATERIAL_INVENTORY_ORG",
                    subject_id=subject_id,
                    subject_label=material_code,
                    subject_context={
                        "material_code": material_code,
                        "inventory_org": inventory_org,
                    },
                    observation_date=observation_date,
                    inv_amount=inventory_amount,
                ),
            },
            sources=["inventory_snapshots", "sales"],
            period=observation_date,
        )
        all_hits.append(hit)
    return all_hits, observation_date


def _very_old_inventory_hits(
    store: DuckDBStore,
    thresholds: RuleThresholds,
) -> tuple[list[RuleHit], str]:
    """B3 超长库龄（365+ 天）库存。"""

    result = get_inventory_very_old_features(store)
    all_hits: list[RuleHit] = []
    observation_date = ""

    for row in result.rows:
        observation_date = _text(row, 0).split("T", maxsplit=1)[0]
        material_code = _text(row, 1)
        inventory_org = _text(row, 2)
        very_old_amount = _number_value(row, 3)
        very_old_quantity = _number_value(row, 4)
        if very_old_amount < thresholds.very_old_inventory_amount:
            continue
        subject_id = f"{material_code}|{inventory_org}"
        admission_key = f"INV|{subject_id}"
        hit = _hit(
            admission_key=admission_key,
            rule_id="INV_VERY_OLD_STOCK",
            rule_name="超长库龄库存",
            severity="HIGH",
            exposure_amount=very_old_amount,
            reason=(
                f"库龄超 365 天库存 {_money(very_old_amount)}"
                f"（{very_old_quantity:.0f} 件），沉淀风险高。"
            ),
            metrics={
                "very_old_amount": very_old_amount,
                "very_old_quantity": very_old_quantity,
                _ENTITY_KEY: _entity_meta(
                    investigation_profile="INVENTORY",
                    subject_type="MATERIAL_INVENTORY_ORG",
                    subject_id=subject_id,
                    subject_label=material_code,
                    subject_context={
                        "material_code": material_code,
                        "inventory_org": inventory_org,
                    },
                    observation_date=observation_date,
                    inv_amount=very_old_amount,
                ),
            },
            sources=["inventory_snapshots"],
            period=observation_date,
        )
        all_hits.append(hit)
    return all_hits, observation_date


def _extension_hits(
    store: DuckDBStore,
    thresholds: RuleThresholds,
) -> tuple[list[RuleHit], str]:
    """A5 多次展期：同一客户展期次数达到阈值。"""

    result = get_extension_rule_features(store)
    all_hits: list[RuleHit] = []
    observation_date = _latest_ar_date(store)

    for row in result.rows:
        customer_id = _text(row, 0)
        customer_name = _text(row, 1)
        extension_count = int(_number_value(row, 2))
        if extension_count < thresholds.extension_count_min:
            continue
        admission_key = f"AR|{customer_id}"
        hit = _hit(
            admission_key=admission_key,
            rule_id="AR_EXTENSION_ABUSE",
            rule_name="多次展期客户",
            severity="MEDIUM",
            exposure_amount=0.0,
            reason=f"客户累计展期 {extension_count} 次，存在反复推迟还款风险。",
            metrics={
                "extension_count": extension_count,
                _ENTITY_KEY: _entity_meta(
                    investigation_profile="RECEIVABLES",
                    subject_type="CUSTOMER",
                    subject_id=customer_id,
                    subject_label=f"{customer_id} {customer_name}".strip(),
                    subject_context={"customer_id": customer_id, "customer_name": customer_name},
                    observation_date=observation_date,
                    ar_balance=None,
                ),
            },
            sources=["extensions"],
            period=observation_date,
        )
        all_hits.append(hit)
    return all_hits, observation_date


def _penalty_interest_hits(
    store: DuckDBStore,
    thresholds: RuleThresholds,
) -> tuple[list[RuleHit], str]:
    """A6 高额罚息：客户累计逾期罚息达到阈值。"""

    result = get_penalty_interest_features(store)
    all_hits: list[RuleHit] = []
    observation_date = _latest_ar_date(store)

    for row in result.rows:
        customer_id = _text(row, 0)
        customer_name = _text(row, 1)
        penalty_interest = _number_value(row, 2)
        if penalty_interest < thresholds.penalty_interest_amount:
            continue
        admission_key = f"AR|{customer_id}"
        hit = _hit(
            admission_key=admission_key,
            rule_id="AR_PENALTY_INTEREST_HIGH",
            rule_name="高额逾期罚息",
            severity="MEDIUM",
            exposure_amount=penalty_interest,
            reason=f"客户累计逾期罚息 {_money(penalty_interest)}，逾期行为严重。",
            metrics={
                "penalty_interest": penalty_interest,
                _ENTITY_KEY: _entity_meta(
                    investigation_profile="RECEIVABLES",
                    subject_type="CUSTOMER",
                    subject_id=customer_id,
                    subject_label=f"{customer_id} {customer_name}".strip(),
                    subject_context={"customer_id": customer_id, "customer_name": customer_name},
                    observation_date=observation_date,
                    ar_balance=None,
                ),
            },
            sources=["payments"],
            period=observation_date,
        )
        all_hits.append(hit)
    return all_hits, observation_date


def _stale_ratio_hits(
    store: DuckDBStore,
    thresholds: RuleThresholds,
) -> tuple[list[RuleHit], str]:
    """B2 呆滞占比过高：180 天以上库存占比高且金额达阈值。"""

    result = get_inventory_stale_ratio_features(store)
    all_hits: list[RuleHit] = []
    observation_date = ""

    for row in result.rows:
        observation_date = _text(row, 0).split("T", maxsplit=1)[0]
        material_code = _text(row, 1)
        inventory_org = _text(row, 2)
        inventory_amount = _number_value(row, 3)
        stale_amount = _number_value(row, 4)
        stale_rate = _value(row, 5)
        stale_rate = None if stale_rate is None else _number_value(row, 5)
        if (
            stale_rate is None
            or stale_rate < thresholds.stale_ratio_threshold
            or stale_amount < thresholds.stale_ratio_amount
        ):
            continue
        subject_id = f"{material_code}|{inventory_org}"
        admission_key = f"INV|{subject_id}"
        hit = _hit(
            admission_key=admission_key,
            rule_id="INV_STALE_RATIO_HIGH",
            rule_name="呆滞占比过高",
            severity="MEDIUM",
            exposure_amount=stale_amount,
            reason=f"180天以上库存 {_money(stale_amount)} 占库存 {stale_rate:.0%}，去化风险高。",
            metrics={
                "inventory_amount": inventory_amount,
                "stale_amount": stale_amount,
                "stale_rate": stale_rate,
                _ENTITY_KEY: _entity_meta(
                    investigation_profile="INVENTORY",
                    subject_type="MATERIAL_INVENTORY_ORG",
                    subject_id=subject_id,
                    subject_label=material_code,
                    subject_context={
                        "material_code": material_code,
                        "inventory_org": inventory_org,
                    },
                    observation_date=observation_date,
                    inv_amount=stale_amount,
                ),
            },
            sources=["inventory_snapshots"],
            period=observation_date,
        )
        all_hits.append(hit)
    return all_hits, observation_date


def _overdue_stock_hits(
    store: DuckDBStore,
    thresholds: RuleThresholds,
) -> tuple[list[RuleHit], str]:
    """B4 超期库存：按金额×超期天数综合评估，金额材料性优先。"""

    result = get_inventory_overdue_stock_features(store)
    all_hits: list[RuleHit] = []
    observation_date = ""

    for row in result.rows:
        observation_date = _text(row, 0).split("T", maxsplit=1)[0]
        material_code = _text(row, 1)
        inventory_org = _text(row, 2)
        overdue_amount = _number_value(row, 3)
        max_overdue_days = int(_number_value(row, 4))
        overdue_rows = int(_number_value(row, 5))
        if overdue_rows == 0:
            continue
        if overdue_amount < thresholds.overdue_stock_amount:
            continue
        if max_overdue_days < thresholds.borrow_overdue_days:
            continue
        subject_id = f"{material_code}|{inventory_org}"
        admission_key = f"INV|{subject_id}"
        hit = _hit(
            admission_key=admission_key,
            rule_id="INV_OVERDUE_STOCK",
            rule_name="超期库存",
            severity="MEDIUM",
            exposure_amount=overdue_amount,
            reason=(
                f"超期库存 {_money(overdue_amount)}，共 {overdue_rows} 条超期记录，"
                f"最大超期 {max_overdue_days} 天。"
            ),
            metrics={
                "overdue_amount": overdue_amount,
                "max_overdue_days": max_overdue_days,
                "overdue_rows": overdue_rows,
                _ENTITY_KEY: _entity_meta(
                    investigation_profile="INVENTORY",
                    subject_type="MATERIAL_INVENTORY_ORG",
                    subject_id=subject_id,
                    subject_label=material_code,
                    subject_context={
                        "material_code": material_code,
                        "inventory_org": inventory_org,
                    },
                    observation_date=observation_date,
                    inv_amount=overdue_amount,
                ),
            },
            sources=["inventory_snapshots"],
            period=observation_date,
        )
        all_hits.append(hit)
    return all_hits, observation_date


def _customer_return_hits(
    store: DuckDBStore,
    thresholds: RuleThresholds,
) -> tuple[list[RuleHit], str]:
    """C1 异常退货集中。"""

    result = get_customer_return_features(store)
    all_hits: list[RuleHit] = []
    observation_date = _latest_ar_date(store)

    for row in result.rows:
        customer_id = _text(row, 0)
        customer_name = _text(row, 1)
        gross_sales = _number_value(row, 2)
        return_amount = _number_value(row, 3)
        if gross_sales <= 0 or return_amount / gross_sales < thresholds.return_ratio_threshold:
            continue
        if return_amount < thresholds.return_amount:
            continue
        admission_key = f"AR|{customer_id}"
        ratio = return_amount / gross_sales
        hit = _hit(
            admission_key=admission_key,
            rule_id="SLS_RETURN_ABNORMAL",
            rule_name="异常退货集中",
            severity="MEDIUM",
            exposure_amount=return_amount,
            reason=(
                f"退货 {_money(return_amount)} 占销售 {_money(gross_sales)} 的 "
                f"{ratio:.0%}，退货占比异常高。"
            ),
            metrics={
                "gross_sales": gross_sales,
                "return_amount": return_amount,
                "return_ratio": ratio,
                _ENTITY_KEY: _entity_meta(
                    investigation_profile="RECEIVABLES",
                    subject_type="CUSTOMER",
                    subject_id=customer_id,
                    subject_label=f"{customer_id} {customer_name}".strip(),
                    subject_context={"customer_id": customer_id, "customer_name": customer_name},
                    observation_date=observation_date,
                    ar_balance=None,
                ),
            },
            sources=["sales"],
            period=observation_date,
        )
        all_hits.append(hit)
    return all_hits, observation_date


def _negative_payment_hits(
    store: DuckDBStore,
    thresholds: RuleThresholds,
) -> tuple[list[RuleHit], str]:
    """C3 负回款（冲销）异常。"""

    result = get_negative_payment_features(store)
    all_hits: list[RuleHit] = []
    observation_date = _latest_ar_date(store)

    for row in result.rows:
        customer_id = _text(row, 0)
        customer_name = _text(row, 1)
        total_payment = _number_value(row, 2)
        negative_payment = _number_value(row, 3)
        if negative_payment < thresholds.negative_payment_amount:
            continue
        if (
            total_payment <= 0
            or negative_payment / total_payment < thresholds.negative_payment_ratio
        ):
            continue
        admission_key = f"AR|{customer_id}"
        ratio = negative_payment / total_payment if total_payment > 0 else 0.0
        hit = _hit(
            admission_key=admission_key,
            rule_id="PAY_OFFSET_ABNORMAL",
            rule_name="负回款异常",
            severity="MEDIUM",
            exposure_amount=negative_payment,
            reason=(
                f"负回款（冲销）{_money(negative_payment)} 占总回款 "
                f"{_money(total_payment)} 的 {ratio:.0%}，冲销占比异常高。"
            ),
            metrics={
                "total_payment": total_payment,
                "negative_payment": negative_payment,
                "negative_ratio": ratio,
                _ENTITY_KEY: _entity_meta(
                    investigation_profile="RECEIVABLES",
                    subject_type="CUSTOMER",
                    subject_id=customer_id,
                    subject_label=f"{customer_id} {customer_name}".strip(),
                    subject_context={"customer_id": customer_id, "customer_name": customer_name},
                    observation_date=observation_date,
                    ar_balance=None,
                ),
            },
            sources=["payments"],
            period=observation_date,
        )
        all_hits.append(hit)
    return all_hits, observation_date


def _aging_payment_hits(
    store: DuckDBStore,
    thresholds: RuleThresholds,
) -> tuple[list[RuleHit], str]:
    """C4 超长账龄回款。"""

    result = get_aging_payment_features(store)
    all_hits: list[RuleHit] = []
    observation_date = _latest_ar_date(store)

    for row in result.rows:
        customer_id = _text(row, 0)
        customer_name = _text(row, 1)
        aging_amount = _number_value(row, 2)
        if aging_amount < thresholds.aging_overdue_amount:
            continue
        admission_key = f"AR|{customer_id}"
        hit = _hit(
            admission_key=admission_key,
            rule_id="PAY_AGING_OVER_365",
            rule_name="超长账龄回款",
            severity="MEDIUM",
            exposure_amount=aging_amount,
            reason=f"回款账龄超 365 天的金额 {_money(aging_amount)}，长期挂账。",
            metrics={
                "aging_amount": aging_amount,
                _ENTITY_KEY: _entity_meta(
                    investigation_profile="RECEIVABLES",
                    subject_type="CUSTOMER",
                    subject_id=customer_id,
                    subject_label=f"{customer_id} {customer_name}".strip(),
                    subject_context={"customer_id": customer_id, "customer_name": customer_name},
                    observation_date=observation_date,
                    ar_balance=None,
                ),
            },
            sources=["payments"],
            period=observation_date,
        )
        all_hits.append(hit)
    return all_hits, observation_date


def _negative_margin_hits(
    store: DuckDBStore,
    thresholds: RuleThresholds,
) -> tuple[list[RuleHit], str]:
    """D1 负毛利合同。"""

    result = get_negative_margin_features(store)
    all_hits: list[RuleHit] = []
    observation_date = _latest_ar_date(store)

    for row in result.rows:
        customer_id = _text(row, 0)
        customer_name = _text(row, 1)
        margin_loss = _number_value(row, 2)
        contract_numbers = _text(row, 3)
        if not customer_id:
            continue
        if margin_loss < thresholds.negative_margin_loss:
            continue
        admission_key = f"AR|{customer_id}"
        hit = _hit(
            admission_key=admission_key,
            rule_id="CON_NEGATIVE_MARGIN",
            rule_name="负毛利合同",
            severity="HIGH",
            exposure_amount=margin_loss,
            reason=(
                f"负毛利合同累计亏损 {_money(margin_loss)}。"
                + (f"合同号：{contract_numbers}。" if contract_numbers else "")
            ),
            metrics={
                "margin_loss": margin_loss,
                "contract_number": contract_numbers,
                "contract_numbers": contract_numbers,
                _ENTITY_KEY: _entity_meta(
                    investigation_profile="RECEIVABLES",
                    subject_type="CUSTOMER",
                    subject_id=customer_id,
                    subject_label=f"{customer_id} {customer_name}".strip(),
                    subject_context={
                        "customer_id": customer_id,
                        "customer_name": customer_name,
                        "contract_number": contract_numbers,
                        "contract_numbers": contract_numbers,
                    },
                    observation_date=observation_date,
                    ar_balance=None,
                ),
            },
            sources=["contracts"],
            period=observation_date,
        )
        all_hits.append(hit)
    return all_hits, observation_date


def _margin_optimistic_hits(
    store: DuckDBStore,
    thresholds: RuleThresholds,
) -> tuple[list[RuleHit], str]:
    """D2 实估毛利严重高估。"""

    result = get_margin_optimistic_features(store)
    all_hits: list[RuleHit] = []
    observation_date = _latest_ar_date(store)

    customer_contracts: dict[str, list[tuple[str, str, float, float, float]]] = {}
    for row in result.rows:
        contract_number = _text(row, 0)
        customer_name = _text(row, 1)
        customer_id = _text(row, 2)
        contract_amount = _number_value(row, 3)
        weighted_est_margin = _number_value(row, 4)
        weighted_act_margin = _number_value(row, 5)
        if not customer_id or not contract_number:
            continue
        if weighted_est_margin - weighted_act_margin < thresholds.margin_gap:
            continue
        if weighted_act_margin >= thresholds.margin_actual_max:
            continue
        customer_contracts.setdefault(customer_id, []).append(
            (
                contract_number,
                customer_name,
                contract_amount,
                weighted_est_margin,
                weighted_act_margin,
            )
        )

    for customer_id, contracts in customer_contracts.items():
        contract_numbers = sorted({item[0] for item in contracts if item[0]})
        contract_label = "、".join(contract_numbers)
        customer_name = next((item[1] for item in contracts if item[1]), "")
        contract_amount = sum(item[2] for item in contracts)
        amount_base = sum(item[2] for item in contracts if item[2] != 0)
        weighted_est_margin = (
            sum(item[2] * item[3] for item in contracts) / amount_base if amount_base else 0.0
        )
        weighted_act_margin = (
            sum(item[2] * item[4] for item in contracts) / amount_base if amount_base else 0.0
        )
        admission_key = f"AR|{customer_id}"
        hit = _hit(
            admission_key=admission_key,
            rule_id="CON_MARGIN_OPTIMISTIC",
            rule_name="实估毛利严重高估",
            severity="MEDIUM",
            exposure_amount=contract_amount,
            reason=(
                f"客户 {customer_id} 的合同 {contract_label} 均满足实估毛利严重高估条件，"
                f"共 {len(contract_numbers)} 份、合同金额 {_money(contract_amount)}，"
                "需要调查预估依据为何失效。"
            ),
            metrics={
                "contract_number": contract_label,
                "contract_numbers": contract_label,
                "contract_count": len(contract_numbers),
                "contract_amount": contract_amount,
                "estimated_margin": weighted_est_margin,
                "actual_margin": weighted_act_margin,
                _ENTITY_KEY: _entity_meta(
                    investigation_profile="RECEIVABLES",
                    subject_type="CUSTOMER",
                    subject_id=customer_id,
                    subject_label=f"{customer_id} {customer_name}".strip(),
                    subject_context={
                        "customer_id": customer_id,
                        "customer_name": customer_name,
                        "contract_number": contract_label,
                        "contract_numbers": contract_label,
                    },
                    observation_date=observation_date,
                    ar_balance=None,
                ),
            },
            sources=["contracts"],
            period=observation_date,
        )
        all_hits.append(hit)
    return all_hits, observation_date


def _term_overage_hits(
    store: DuckDBStore,
    thresholds: RuleThresholds,
) -> tuple[list[RuleHit], str]:
    """D3 实际账期远超约定。"""

    result = get_term_overage_features(store)
    all_hits: list[RuleHit] = []
    observation_date = _latest_ar_date(store)

    for row in result.rows:
        customer_id = _text(row, 0)
        customer_name = _text(row, 1)
        overage_count = int(_number_value(row, 2))
        contract_amount = _number_value(row, 3)
        max_overage = int(_number_value(row, 4))
        contract_numbers = _text(row, 5)
        if not customer_id:
            continue
        if contract_amount < thresholds.term_overage_amount:
            continue
        admission_key = f"AR|{customer_id}"
        hit = _hit(
            admission_key=admission_key,
            rule_id="CON_TERM_OVERAGE",
            rule_name="实际账期远超约定",
            severity="MEDIUM",
            exposure_amount=contract_amount,
            reason=(
                f"{overage_count} 份合同实际账期超约定 ≥120 天，最大超期 {max_overage} 天。"
                + (f"合同号：{contract_numbers}。" if contract_numbers else "")
            ),
            metrics={
                "overage_contract_count": overage_count,
                "contract_amount": contract_amount,
                "max_overage_days": max_overage,
                "contract_number": contract_numbers,
                "contract_numbers": contract_numbers,
                _ENTITY_KEY: _entity_meta(
                    investigation_profile="RECEIVABLES",
                    subject_type="CUSTOMER",
                    subject_id=customer_id,
                    subject_label=f"{customer_id} {customer_name}".strip(),
                    subject_context={
                        "customer_id": customer_id,
                        "customer_name": customer_name,
                        "contract_number": contract_numbers,
                        "contract_numbers": contract_numbers,
                    },
                    observation_date=observation_date,
                    ar_balance=None,
                ),
            },
            sources=["contracts"],
            period=observation_date,
        )
        all_hits.append(hit)
    return all_hits, observation_date


def collect_rule_hits(
    store: DuckDBStore,
    thresholds: RuleThresholds | None = None,
) -> RuleHitBatch:
    """执行所有确定性规则，只返回原始命中，不创建案件。"""

    active_thresholds = thresholds or RuleThresholds()
    ar_hits, ar_period = _receivable_hits(store, active_thresholds)
    unpaid_hits, unpaid_period = _unpaid_sales_hits(store, active_thresholds)
    inventory_hits, inventory_period = _inventory_hits(store, active_thresholds)
    zero_hits, zero_period = _zero_sales_inventory_hits(store, active_thresholds)
    veryold_hits, veryold_period = _very_old_inventory_hits(store, active_thresholds)
    extension_hits, extension_period = _extension_hits(store, active_thresholds)
    penalty_hits, penalty_period = _penalty_interest_hits(store, active_thresholds)
    staleratio_hits, staleratio_period = _stale_ratio_hits(store, active_thresholds)
    borrow_hits, borrow_period = _overdue_stock_hits(store, active_thresholds)
    return_hits, return_period = _customer_return_hits(store, active_thresholds)
    negpay_hits, negpay_period = _negative_payment_hits(store, active_thresholds)
    aging_hits, aging_period = _aging_payment_hits(store, active_thresholds)
    negmargin_hits, negmargin_period = _negative_margin_hits(store, active_thresholds)
    marginopt_hits, marginopt_period = _margin_optimistic_hits(store, active_thresholds)
    term_hits, term_period = _term_overage_hits(store, active_thresholds)
    hits = [
        *ar_hits,
        *unpaid_hits,
        *extension_hits,
        *penalty_hits,
        *return_hits,
        *negpay_hits,
        *aging_hits,
        *negmargin_hits,
        *marginopt_hits,
        *term_hits,
        *inventory_hits,
        *zero_hits,
        *veryold_hits,
        *staleratio_hits,
        *borrow_hits,
    ]
    observation_date = max(
        ar_period,
        unpaid_period,
        extension_period,
        penalty_period,
        return_period,
        negpay_period,
        aging_period,
        negmargin_period,
        marginopt_period,
        term_period,
        inventory_period,
        zero_period,
        veryold_period,
        staleratio_period,
        borrow_period,
    )
    return RuleHitBatch(hits=tuple(hits), observation_date=observation_date)


# 成交前入口信号的版本和口径来源；与批量扫描规则同库维护。
PRE_TRANSACTION_REVIEW_VERSION = "2.0.0"
PRE_TRANSACTION_THRESHOLD_SOURCE = "客户同业务类型历史分布与成交前必查流程"

# 订单场景到入口优先级的映射。场景描述的是演示输入的生成方式，映射只用于让案件
# 队列按风险高低排序，最终优先级由 Agent 调查后重新给出。
_SCENARIO_PRIORITY = {
    "NORMAL": "LOW",
    "BORDERLINE": "MEDIUM",
    "ANOMALY": "HIGH",
}


def pre_transaction_review_hit(
    simulated: SimulatedOrder,
    *,
    case_id: str,
    list_status: str,
) -> RuleHit:
    """生成模拟新交易的成交前必查入口信号，并给出规则层的入口评分。

    所有模拟订单一律需要进入统一案件流程接受 Agent 调查，这是硬性要求；本信号只表达
    "该订单需要成交前审查"。severity 是规则层的入口评分（订单场景 + 名单状态），
    不替代后续 Agent 对风险程度的判断。
    """

    scenario_value = simulated.scenario.value
    priority = _SCENARIO_PRIORITY[scenario_value]
    if scenario_value == "NORMAL":
        reason = "新交易在成交前进入Agent基线调查。"
    elif scenario_value == "BORDERLINE":
        reason = "拟交易金额处于客户同业务历史分布的偏高区间，需要核对回款和敞口。"
    else:
        reason = "拟交易金额显著高于客户同业务历史P90，需要在成交前调查。"
    if list_status == "黑名单":
        priority = "HIGH"
        reason += " 当前授信主数据标记为黑名单，必须人工复核。"
    generated_date = simulated.generated_at.split("T", maxsplit=1)[0]
    context: dict[str, DatabaseScalar] = {
        "simulation_id": simulated.simulation_id,
        "customer_id": simulated.customer_id,
        "customer_name": simulated.customer_name,
        "amount_yuan": simulated.amount_yuan,
        "proposed_term_days": simulated.proposed_term_days,
        "expected_margin_rate": simulated.expected_margin_rate,
        "scenario": simulated.scenario.value,
        "historical_order_count": simulated.historical_order_count,
        "historical_median_amount_yuan": simulated.distribution_summary["median_yuan"],
        "historical_p90_amount_yuan": simulated.distribution_summary["p90_yuan"],
        "list_status_at_intake": list_status,
        "generated_at": simulated.generated_at,
        "simulated": True,
    }
    return RuleHit(
        rule_hit_id=_short_id("hit", f"{case_id}|PRE_TRANSACTION_REVIEW|{RULE_VERSION}"),
        subject=RuleSubject(
            admission_key=case_id,
            investigation_profile="PRE_TRANSACTION",
            subject_type="CUSTOMER",
            subject_id=simulated.customer_id,
            subject_label=f"{simulated.customer_id} {simulated.customer_name}",
            subject_context=context,
            observation_date=generated_date,
            exposure_amount=simulated.amount_yuan,
        ),
        rule_id="PRE_TRANSACTION_REVIEW",
        rule_name="新交易事前调查",
        rule_version=RULE_VERSION,
        severity=priority,
        exposure_amount=simulated.amount_yuan,
        reason=reason,
        metrics={
            "proposed_amount_yuan": simulated.amount_yuan,
            "historical_median_yuan": simulated.distribution_summary["median_yuan"],
            "historical_p90_yuan": simulated.distribution_summary["p90_yuan"],
            "scenario": simulated.scenario.value,
            "historical_order_count": simulated.historical_order_count,
            "list_status_at_intake": list_status,
        },
        threshold_source=PRE_TRANSACTION_THRESHOLD_SOURCE,
        threshold_version=simulated.source_snapshot_id,
        sources=("sales", "payments", "customer_credit"),
        period=generated_date,
    )

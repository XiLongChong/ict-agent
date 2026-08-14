"""版本化风险规则、组合模式检测与案件草稿生成。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from ict_agent.data import CaseWrite, DatabaseScalar, DuckDBStore, RuleHitWrite, RuleRunWrite
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


@dataclass(frozen=True)
class RuleScanDraft:
    """尚未写入案件库的一次完整扫描。"""

    run: RuleRunWrite
    cases: tuple[CaseWrite, ...]
    hits: tuple[RuleHitWrite, ...]


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


def _priority(hits: list[RuleHitWrite]) -> str:
    order = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    return max((hit.severity for hit in hits), key=order.__getitem__)


_ENTITY_KEY = "_entity"


def _tag_hit(hit: RuleHitWrite, entity_meta: dict[str, object]) -> RuleHitWrite:
    """把实体元数据写入 hit 的 metrics，供合并时使用。"""

    merged = dict(hit.metrics)
    merged[_ENTITY_KEY] = entity_meta
    return RuleHitWrite(
        rule_hit_id=hit.rule_hit_id,
        case_id=hit.case_id,
        rule_id=hit.rule_id,
        rule_name=hit.rule_name,
        rule_version=hit.rule_version,
        severity=hit.severity,
        exposure_amount=hit.exposure_amount,
        reason=hit.reason,
        metrics=merged,
        threshold_source=hit.threshold_source,
        sources=hit.sources,
        period=hit.period,
    )


def _entity_meta(
    *,
    case_type: str,
    entity_type: str,
    entity_id: str,
    entity_label: str,
    entity_context: dict[str, object],
    observation_date: str,
    ar_balance: float | None = None,
    inv_amount: float | None = None,
) -> dict[str, object]:
    """构造实体元数据，供合并案件时还原 CaseWrite 字段。"""

    return {
        "case_type": case_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "entity_label": entity_label,
        "entity_context": entity_context,
        "observation_date": observation_date,
        "ar_balance": ar_balance,
        "inv_amount": inv_amount,
    }


def _merge_hits_into_cases(
    hits: list[RuleHitWrite],
) -> tuple[list[CaseWrite], list[RuleHitWrite]]:
    """把属于同一实体（客户 或 物料|仓库）的全部 hit 合并成一个案件。
    实体键用 hit.case_id（已统一为 AR|cid 或 INV|mat|org）。
    返回 (cases, clean_hits)，clean_hits 已剥离内部 _entity 元数据，避免污染 RuleHit.metrics。
    """

    from collections import OrderedDict

    grouped: dict[str, list[RuleHitWrite]] = OrderedDict()
    clean_hits: list[RuleHitWrite] = []
    for hit in hits:
        grouped.setdefault(hit.case_id, []).append(hit)
        clean_hits.append(_strip_entity(hit))

    cases: list[CaseWrite] = []
    for case_id, group in grouped.items():
        meta: dict[str, object] = {}
        for hit in group:
            ent = hit.metrics.get(_ENTITY_KEY)
            if isinstance(ent, dict):
                meta = ent
                break
        case_type = str(meta.get("case_type", "ACCOUNTS_RECEIVABLE"))
        entity_type = str(meta.get("entity_type", "CUSTOMER"))
        entity_id = str(meta.get("entity_id", case_id))
        entity_label = str(meta.get("entity_label", entity_id))
        entity_context = meta.get("entity_context")
        observation_date = str(meta.get("observation_date", ""))
        # 暴露金额：应收域取应收余额，库存域取库存金额；缺省用组内最大命中暴露
        ar_balance = meta.get("ar_balance")
        inv_amount = meta.get("inv_amount")
        ar_balance_num = float(ar_balance) if isinstance(ar_balance, (int, float)) else None
        inv_amount_num = float(inv_amount) if isinstance(inv_amount, (int, float)) else None
        if case_type == "INVENTORY" and inv_amount_num is not None:
            exposure = inv_amount_num
        elif ar_balance_num is not None:
            exposure = ar_balance_num
        else:
            exposure = max((h.exposure_amount for h in group), default=0.0)
        primary_hit = min(
            group,
            key=lambda hit: (
                {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}.get(hit.severity, 4),
                hit.rule_id,
            ),
        )
        cases.append(
            CaseWrite(
                case_id=case_id,
                case_type=case_type,
                entity_type=entity_type,
                entity_id=entity_id,
                entity_label=entity_label,
                entity_context=dict(entity_context) if isinstance(entity_context, dict) else {},
                observation_date=observation_date,
                priority=_priority(group),
                exposure_amount=exposure,
                summary=(
                    f"主要风险：{primary_hit.rule_name}；需结合 {len(group)} 条规则信号调查核实。"
                ),
                rule_hit_count=len(group),
                rule_set_version=RULE_SET_VERSION,
                created_at=max((h.period for h in group), default=""),
            )
        )
    return cases, clean_hits


def _strip_entity(hit: RuleHitWrite) -> RuleHitWrite:
    """返回去掉了 _entity 内部键的 hit，保持 metrics 为可校验的 JsonScalar。"""

    if _ENTITY_KEY not in hit.metrics:
        return hit
    merged = {k: v for k, v in hit.metrics.items() if k != _ENTITY_KEY}
    return RuleHitWrite(
        rule_hit_id=hit.rule_hit_id,
        case_id=hit.case_id,
        rule_id=hit.rule_id,
        rule_name=hit.rule_name,
        rule_version=hit.rule_version,
        severity=hit.severity,
        exposure_amount=hit.exposure_amount,
        reason=hit.reason,
        metrics=merged,
        threshold_source=hit.threshold_source,
        sources=hit.sources,
        period=hit.period,
    )


def _money(value: float) -> str:
    if abs(value) >= 100_000_000:
        return f"{value / 100_000_000:.2f} 亿元"
    if abs(value) >= 10_000:
        return f"{value / 10_000:.2f} 万元"
    return f"{value:.2f} 元"


def _hit(
    *,
    case_id: str,
    rule_id: str,
    rule_name: str,
    severity: str,
    exposure_amount: float,
    reason: str,
    metrics: dict[str, object],
    sources: list[str],
    period: str,
) -> RuleHitWrite:
    hit_id = _short_id("hit", f"{case_id}|{rule_id}|{RULE_VERSION}")
    return RuleHitWrite(
        rule_hit_id=hit_id,
        case_id=case_id,
        rule_id=rule_id,
        rule_name=rule_name,
        rule_version=RULE_VERSION,
        severity=severity,
        exposure_amount=exposure_amount,
        reason=reason,
        metrics=metrics,
        threshold_source=THRESHOLD_SOURCE,
        sources=sources,
        period=period,
    )


def _receivable_cases(
    store: DuckDBStore,
    thresholds: RuleThresholds,
    created_at: str,
) -> tuple[list[CaseWrite], list[RuleHitWrite], str]:
    result = get_receivable_rule_features(store)
    cases: list[CaseWrite] = []
    all_hits: list[RuleHitWrite] = []
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
        case_id = f"AR|{customer_id}"
        hits: list[RuleHitWrite] = []

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
                case_type="ACCOUNTS_RECEIVABLE",
                entity_type="CUSTOMER",
                entity_id=customer_id,
                entity_label=f"{customer_id} {customer_name}".strip(),
                entity_context={"customer_id": customer_id, "customer_name": customer_name},
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
                    case_id=case_id,
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
                    case_id=case_id,
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
                    case_id=case_id,
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
                    case_id=case_id,
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
                    case_id=case_id,
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
                    case_id=case_id,
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
                        case_id=case_id,
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
        cases.append(
            CaseWrite(
                case_id=case_id,
                case_type="ACCOUNTS_RECEIVABLE",
                entity_type="CUSTOMER",
                entity_id=customer_id,
                entity_label=f"{customer_id} {customer_name}".strip(),
                entity_context={"customer_id": customer_id, "customer_name": customer_name},
                observation_date=observation_date,
                priority=_priority(hits),
                exposure_amount=ar_amount,
                summary=(
                    f"最新应收 {_money(ar_amount)}，超期 {_money(overdue_amount)}，"
                    f"客户当前为黑名单，命中 {len(hits)} 条风险规则。"
                    if list_status == 2
                    else (
                        f"最新应收 {_money(ar_amount)}，超期 {_money(overdue_amount)}，"
                        f"客户仍在经营且未进入黑名单，命中 {len(hits)} 条早期预警规则。"
                    )
                ),
                rule_hit_count=len(hits),
                rule_set_version=RULE_SET_VERSION,
                created_at=created_at,
            )
        )
    return cases, all_hits, observation_date


def _inventory_cases(
    store: DuckDBStore,
    thresholds: RuleThresholds,
    created_at: str,
) -> tuple[list[CaseWrite], list[RuleHitWrite], str]:
    result = get_inventory_rule_features(store)
    cases: list[CaseWrite] = []
    all_hits: list[RuleHitWrite] = []
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
        entity_id = f"{material_code}|{inventory_org}"
        case_id = f"INV|{entity_id}"
        hits: list[RuleHitWrite] = []
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
                case_type="INVENTORY",
                entity_type="MATERIAL_INVENTORY_ORG",
                entity_id=entity_id,
                entity_label=material_code,
                entity_context={
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
                    case_id=case_id,
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
                    case_id=case_id,
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
                    case_id=case_id,
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
        cases.append(
            CaseWrite(
                case_id=case_id,
                case_type="INVENTORY",
                entity_type="MATERIAL_INVENTORY_ORG",
                entity_id=entity_id,
                entity_label=material_code,
                entity_context={
                    "material_code": material_code,
                    "inventory_org": inventory_org,
                },
                observation_date=observation_date,
                priority=_priority(hits),
                exposure_amount=inventory_amount,
                summary=(
                    f"当前库存 {_money(inventory_amount)}，较上季变化 "
                    f"{_money(inventory_growth)}，命中 {len(hits)} 条调查规则。"
                ),
                rule_hit_count=len(hits),
                rule_set_version=RULE_SET_VERSION,
                created_at=created_at,
            )
        )
    return cases, all_hits, observation_date


def _unpaid_sales_cases(
    store: DuckDBStore,
    thresholds: RuleThresholds,
    created_at: str,
) -> tuple[list[CaseWrite], list[RuleHitWrite], str]:
    """A2 长期销售未回款：出库超过 90 天仍无正回款的销售订单，按客户聚合。"""

    result = get_unpaid_sales_features(store)
    cases: list[CaseWrite] = []
    all_hits: list[RuleHitWrite] = []
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
        case_id = f"AR|{customer_id}"
        hit = _hit(
            case_id=case_id,
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
                    case_type="ACCOUNTS_RECEIVABLE",
                    entity_type="CUSTOMER",
                    entity_id=customer_id,
                    entity_label=f"{customer_id} {customer_name}".strip(),
                    entity_context={"customer_id": customer_id, "customer_name": customer_name},
                    observation_date=observation_date,
                    ar_balance=unpaid_ge90_amount,
                ),
            },
            sources=["sales", "payments"],
            period=observation_date,
        )
        all_hits.append(hit)
        cases.append(
            CaseWrite(
                case_id=case_id,
                case_type="ACCOUNTS_RECEIVABLE",
                entity_type="CUSTOMER",
                entity_id=customer_id,
                entity_label=f"{customer_id} {customer_name}".strip(),
                entity_context={"customer_id": customer_id, "customer_name": customer_name},
                observation_date=observation_date,
                priority="HIGH",
                exposure_amount=unpaid_ge90_amount,
                summary=(
                    f"{unpaid_order_count} 个订单超 90 天未回款，"
                    f"未回款 {_money(unpaid_ge90_amount)}。"
                ),
                rule_hit_count=1,
                rule_set_version=RULE_SET_VERSION,
                created_at=created_at,
            )
        )
    return cases, all_hits, observation_date


def _zero_sales_inventory_cases(
    store: DuckDBStore,
    thresholds: RuleThresholds,
    created_at: str,
) -> tuple[list[CaseWrite], list[RuleHitWrite], str]:
    """B1 高库存但近三个月零销售：广口径潜在呆滞。"""

    result = get_inventory_zero_sales_features(store)
    cases: list[CaseWrite] = []
    all_hits: list[RuleHitWrite] = []
    observation_date = ""

    for row in result.rows:
        observation_date = _text(row, 0).split("T", maxsplit=1)[0]
        material_code = _text(row, 1)
        inventory_org = _text(row, 2)
        inventory_amount = _number_value(row, 3)
        sales_3m = _number_value(row, 4)
        if sales_3m > 0 or inventory_amount < thresholds.zero_sales_inventory_amount:
            continue
        entity_id = f"{material_code}|{inventory_org}"
        case_id = f"INV|{entity_id}"
        hit = _hit(
            case_id=case_id,
            rule_id="INV_ZERO_SALES_STOCK",
            rule_name="高库存但近期零销售",
            severity="HIGH",
            exposure_amount=inventory_amount,
            reason=f"库存 {_money(inventory_amount)}，近三个月无正销售，可能为滞销。",
            metrics={
                "inventory_amount": inventory_amount,
                "sales_3m": sales_3m,
                _ENTITY_KEY: _entity_meta(
                    case_type="INVENTORY",
                    entity_type="MATERIAL_INVENTORY_ORG",
                    entity_id=entity_id,
                    entity_label=material_code,
                    entity_context={"material_code": material_code, "inventory_org": inventory_org},
                    observation_date=observation_date,
                    inv_amount=inventory_amount,
                ),
            },
            sources=["inventory_snapshots", "sales"],
            period=observation_date,
        )
        all_hits.append(hit)
        cases.append(
            CaseWrite(
                case_id=case_id,
                case_type="INVENTORY",
                entity_type="MATERIAL_INVENTORY_ORG",
                entity_id=entity_id,
                entity_label=material_code,
                entity_context={"material_code": material_code, "inventory_org": inventory_org},
                observation_date=observation_date,
                priority="HIGH",
                exposure_amount=inventory_amount,
                summary=f"库存 {_money(inventory_amount)} 近三个月零销售，疑似滞销。",
                rule_hit_count=1,
                rule_set_version=RULE_SET_VERSION,
                created_at=created_at,
            )
        )
    return cases, all_hits, observation_date


def _very_old_inventory_cases(
    store: DuckDBStore,
    thresholds: RuleThresholds,
    created_at: str,
) -> tuple[list[CaseWrite], list[RuleHitWrite], str]:
    """B3 超长库龄（365+ 天）库存。"""

    result = get_inventory_very_old_features(store)
    cases: list[CaseWrite] = []
    all_hits: list[RuleHitWrite] = []
    observation_date = ""

    for row in result.rows:
        observation_date = _text(row, 0).split("T", maxsplit=1)[0]
        material_code = _text(row, 1)
        inventory_org = _text(row, 2)
        very_old_amount = _number_value(row, 3)
        very_old_quantity = _number_value(row, 4)
        if very_old_amount < thresholds.very_old_inventory_amount:
            continue
        entity_id = f"{material_code}|{inventory_org}"
        case_id = f"INV|{entity_id}"
        hit = _hit(
            case_id=case_id,
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
                    case_type="INVENTORY",
                    entity_type="MATERIAL_INVENTORY_ORG",
                    entity_id=entity_id,
                    entity_label=material_code,
                    entity_context={"material_code": material_code, "inventory_org": inventory_org},
                    observation_date=observation_date,
                    inv_amount=very_old_amount,
                ),
            },
            sources=["inventory_snapshots"],
            period=observation_date,
        )
        all_hits.append(hit)
        cases.append(
            CaseWrite(
                case_id=case_id,
                case_type="INVENTORY",
                entity_type="MATERIAL_INVENTORY_ORG",
                entity_id=entity_id,
                entity_label=material_code,
                entity_context={"material_code": material_code, "inventory_org": inventory_org},
                observation_date=observation_date,
                priority="HIGH",
                exposure_amount=very_old_amount,
                summary=f"库龄超 365 天库存 {_money(very_old_amount)}，沉淀风险高。",
                rule_hit_count=1,
                rule_set_version=RULE_SET_VERSION,
                created_at=created_at,
            )
        )
    return cases, all_hits, observation_date


def _extension_cases(
    store: DuckDBStore,
    thresholds: RuleThresholds,
    created_at: str,
) -> tuple[list[CaseWrite], list[RuleHitWrite], str]:
    """A5 多次展期：同一客户展期次数达到阈值。"""

    result = get_extension_rule_features(store)
    cases: list[CaseWrite] = []
    all_hits: list[RuleHitWrite] = []
    observation_date = _latest_ar_date(store)

    for row in result.rows:
        customer_id = _text(row, 0)
        customer_name = _text(row, 1)
        extension_count = int(_number_value(row, 2))
        if extension_count < thresholds.extension_count_min:
            continue
        case_id = f"AR|{customer_id}"
        hit = _hit(
            case_id=case_id,
            rule_id="AR_EXTENSION_ABUSE",
            rule_name="多次展期客户",
            severity="MEDIUM",
            exposure_amount=0.0,
            reason=f"客户累计展期 {extension_count} 次，存在反复推迟还款风险。",
            metrics={
                "extension_count": extension_count,
                _ENTITY_KEY: _entity_meta(
                    case_type="ACCOUNTS_RECEIVABLE",
                    entity_type="CUSTOMER",
                    entity_id=customer_id,
                    entity_label=f"{customer_id} {customer_name}".strip(),
                    entity_context={"customer_id": customer_id, "customer_name": customer_name},
                    observation_date=observation_date,
                    ar_balance=None,
                ),
            },
            sources=["extensions"],
            period=observation_date,
        )
        all_hits.append(hit)
        cases.append(
            CaseWrite(
                case_id=case_id,
                case_type="ACCOUNTS_RECEIVABLE",
                entity_type="CUSTOMER",
                entity_id=customer_id,
                entity_label=f"{customer_id} {customer_name}".strip(),
                entity_context={"customer_id": customer_id, "customer_name": customer_name},
                observation_date=observation_date,
                priority="MEDIUM",
                exposure_amount=0.0,
                summary=f"客户累计展期 {extension_count} 次。",
                rule_hit_count=1,
                rule_set_version=RULE_SET_VERSION,
                created_at=created_at,
            )
        )
    return cases, all_hits, observation_date


def _penalty_interest_cases(
    store: DuckDBStore,
    thresholds: RuleThresholds,
    created_at: str,
) -> tuple[list[CaseWrite], list[RuleHitWrite], str]:
    """A6 高额罚息：客户累计逾期罚息达到阈值。"""

    result = get_penalty_interest_features(store)
    cases: list[CaseWrite] = []
    all_hits: list[RuleHitWrite] = []
    observation_date = _latest_ar_date(store)

    for row in result.rows:
        customer_id = _text(row, 0)
        customer_name = _text(row, 1)
        penalty_interest = _number_value(row, 2)
        if penalty_interest < thresholds.penalty_interest_amount:
            continue
        case_id = f"AR|{customer_id}"
        hit = _hit(
            case_id=case_id,
            rule_id="AR_PENALTY_INTEREST_HIGH",
            rule_name="高额逾期罚息",
            severity="MEDIUM",
            exposure_amount=penalty_interest,
            reason=f"客户累计逾期罚息 {_money(penalty_interest)}，逾期行为严重。",
            metrics={
                "penalty_interest": penalty_interest,
                _ENTITY_KEY: _entity_meta(
                    case_type="ACCOUNTS_RECEIVABLE",
                    entity_type="CUSTOMER",
                    entity_id=customer_id,
                    entity_label=f"{customer_id} {customer_name}".strip(),
                    entity_context={"customer_id": customer_id, "customer_name": customer_name},
                    observation_date=observation_date,
                    ar_balance=None,
                ),
            },
            sources=["payments"],
            period=observation_date,
        )
        all_hits.append(hit)
        cases.append(
            CaseWrite(
                case_id=case_id,
                case_type="ACCOUNTS_RECEIVABLE",
                entity_type="CUSTOMER",
                entity_id=customer_id,
                entity_label=f"{customer_id} {customer_name}".strip(),
                entity_context={"customer_id": customer_id, "customer_name": customer_name},
                observation_date=observation_date,
                priority="MEDIUM",
                exposure_amount=penalty_interest,
                summary=f"客户累计逾期罚息 {_money(penalty_interest)}。",
                rule_hit_count=1,
                rule_set_version=RULE_SET_VERSION,
                created_at=created_at,
            )
        )
    return cases, all_hits, observation_date


def _stale_ratio_cases(
    store: DuckDBStore,
    thresholds: RuleThresholds,
    created_at: str,
) -> tuple[list[CaseWrite], list[RuleHitWrite], str]:
    """B2 呆滞占比过高：180 天以上库存占比高且金额达阈值。"""

    result = get_inventory_stale_ratio_features(store)
    cases: list[CaseWrite] = []
    all_hits: list[RuleHitWrite] = []
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
        entity_id = f"{material_code}|{inventory_org}"
        case_id = f"INV|{entity_id}"
        hit = _hit(
            case_id=case_id,
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
                    case_type="INVENTORY",
                    entity_type="MATERIAL_INVENTORY_ORG",
                    entity_id=entity_id,
                    entity_label=material_code,
                    entity_context={"material_code": material_code, "inventory_org": inventory_org},
                    observation_date=observation_date,
                    inv_amount=stale_amount,
                ),
            },
            sources=["inventory_snapshots"],
            period=observation_date,
        )
        all_hits.append(hit)
        cases.append(
            CaseWrite(
                case_id=case_id,
                case_type="INVENTORY",
                entity_type="MATERIAL_INVENTORY_ORG",
                entity_id=entity_id,
                entity_label=material_code,
                entity_context={"material_code": material_code, "inventory_org": inventory_org},
                observation_date=observation_date,
                priority="MEDIUM",
                exposure_amount=stale_amount,
                summary=f"180天以上库存 {_money(stale_amount)}，占 {stale_rate:.0%}。",
                rule_hit_count=1,
                rule_set_version=RULE_SET_VERSION,
                created_at=created_at,
            )
        )
    return cases, all_hits, observation_date


def _overdue_stock_cases(
    store: DuckDBStore,
    thresholds: RuleThresholds,
    created_at: str,
) -> tuple[list[CaseWrite], list[RuleHitWrite], str]:
    """B4 超期库存：按金额×超期天数综合评估，金额材料性优先。"""

    result = get_inventory_overdue_stock_features(store)
    cases: list[CaseWrite] = []
    all_hits: list[RuleHitWrite] = []
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
        entity_id = f"{material_code}|{inventory_org}"
        case_id = f"INV|{entity_id}"
        hit = _hit(
            case_id=case_id,
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
                    case_type="INVENTORY",
                    entity_type="MATERIAL_INVENTORY_ORG",
                    entity_id=entity_id,
                    entity_label=material_code,
                    entity_context={"material_code": material_code, "inventory_org": inventory_org},
                    observation_date=observation_date,
                    inv_amount=overdue_amount,
                ),
            },
            sources=["inventory_snapshots"],
            period=observation_date,
        )
        all_hits.append(hit)
        cases.append(
            CaseWrite(
                case_id=case_id,
                case_type="INVENTORY",
                entity_type="MATERIAL_INVENTORY_ORG",
                entity_id=entity_id,
                entity_label=material_code,
                entity_context={"material_code": material_code, "inventory_org": inventory_org},
                observation_date=observation_date,
                priority="MEDIUM",
                exposure_amount=overdue_amount,
                summary=f"超期库存 {_money(overdue_amount)}，最大超期 {max_overdue_days} 天。",
                rule_hit_count=1,
                rule_set_version=RULE_SET_VERSION,
                created_at=created_at,
            )
        )
    return cases, all_hits, observation_date


def _customer_return_cases(
    store: DuckDBStore,
    thresholds: RuleThresholds,
    created_at: str,
) -> tuple[list[CaseWrite], list[RuleHitWrite], str]:
    """C1 异常退货集中。"""

    result = get_customer_return_features(store)
    cases: list[CaseWrite] = []
    all_hits: list[RuleHitWrite] = []
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
        case_id = f"AR|{customer_id}"
        ratio = return_amount / gross_sales
        hit = _hit(
            case_id=case_id,
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
                    case_type="ACCOUNTS_RECEIVABLE",
                    entity_type="CUSTOMER",
                    entity_id=customer_id,
                    entity_label=f"{customer_id} {customer_name}".strip(),
                    entity_context={"customer_id": customer_id, "customer_name": customer_name},
                    observation_date=observation_date,
                    ar_balance=None,
                ),
            },
            sources=["sales"],
            period=observation_date,
        )
        all_hits.append(hit)
        cases.append(
            CaseWrite(
                case_id=case_id,
                case_type="ACCOUNTS_RECEIVABLE",
                entity_type="CUSTOMER",
                entity_id=customer_id,
                entity_label=f"{customer_id} {customer_name}".strip(),
                entity_context={"customer_id": customer_id, "customer_name": customer_name},
                observation_date=observation_date,
                priority="MEDIUM",
                exposure_amount=return_amount,
                summary=f"退货 {_money(return_amount)} 占销售 {ratio:.0%}。",
                rule_hit_count=1,
                rule_set_version=RULE_SET_VERSION,
                created_at=created_at,
            )
        )
    return cases, all_hits, observation_date


def _negative_payment_cases(
    store: DuckDBStore,
    thresholds: RuleThresholds,
    created_at: str,
) -> tuple[list[CaseWrite], list[RuleHitWrite], str]:
    """C3 负回款（冲销）异常。"""

    result = get_negative_payment_features(store)
    cases: list[CaseWrite] = []
    all_hits: list[RuleHitWrite] = []
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
        case_id = f"AR|{customer_id}"
        ratio = negative_payment / total_payment if total_payment > 0 else 0.0
        hit = _hit(
            case_id=case_id,
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
                    case_type="ACCOUNTS_RECEIVABLE",
                    entity_type="CUSTOMER",
                    entity_id=customer_id,
                    entity_label=f"{customer_id} {customer_name}".strip(),
                    entity_context={"customer_id": customer_id, "customer_name": customer_name},
                    observation_date=observation_date,
                    ar_balance=None,
                ),
            },
            sources=["payments"],
            period=observation_date,
        )
        all_hits.append(hit)
        cases.append(
            CaseWrite(
                case_id=case_id,
                case_type="ACCOUNTS_RECEIVABLE",
                entity_type="CUSTOMER",
                entity_id=customer_id,
                entity_label=f"{customer_id} {customer_name}".strip(),
                entity_context={"customer_id": customer_id, "customer_name": customer_name},
                observation_date=observation_date,
                priority="MEDIUM",
                exposure_amount=negative_payment,
                summary=f"负回款 {_money(negative_payment)} 占总回款 {ratio:.0%}。",
                rule_hit_count=1,
                rule_set_version=RULE_SET_VERSION,
                created_at=created_at,
            )
        )
    return cases, all_hits, observation_date


def _aging_payment_cases(
    store: DuckDBStore,
    thresholds: RuleThresholds,
    created_at: str,
) -> tuple[list[CaseWrite], list[RuleHitWrite], str]:
    """C4 超长账龄回款。"""

    result = get_aging_payment_features(store)
    cases: list[CaseWrite] = []
    all_hits: list[RuleHitWrite] = []
    observation_date = _latest_ar_date(store)

    for row in result.rows:
        customer_id = _text(row, 0)
        customer_name = _text(row, 1)
        aging_amount = _number_value(row, 2)
        if aging_amount < thresholds.aging_overdue_amount:
            continue
        case_id = f"AR|{customer_id}"
        hit = _hit(
            case_id=case_id,
            rule_id="PAY_AGING_OVER_365",
            rule_name="超长账龄回款",
            severity="MEDIUM",
            exposure_amount=aging_amount,
            reason=f"回款账龄超 365 天的金额 {_money(aging_amount)}，长期挂账。",
            metrics={
                "aging_amount": aging_amount,
                _ENTITY_KEY: _entity_meta(
                    case_type="ACCOUNTS_RECEIVABLE",
                    entity_type="CUSTOMER",
                    entity_id=customer_id,
                    entity_label=f"{customer_id} {customer_name}".strip(),
                    entity_context={"customer_id": customer_id, "customer_name": customer_name},
                    observation_date=observation_date,
                    ar_balance=None,
                ),
            },
            sources=["payments"],
            period=observation_date,
        )
        all_hits.append(hit)
        cases.append(
            CaseWrite(
                case_id=case_id,
                case_type="ACCOUNTS_RECEIVABLE",
                entity_type="CUSTOMER",
                entity_id=customer_id,
                entity_label=f"{customer_id} {customer_name}".strip(),
                entity_context={"customer_id": customer_id, "customer_name": customer_name},
                observation_date=observation_date,
                priority="MEDIUM",
                exposure_amount=aging_amount,
                summary=f"超长账龄回款 {_money(aging_amount)}。",
                rule_hit_count=1,
                rule_set_version=RULE_SET_VERSION,
                created_at=created_at,
            )
        )
    return cases, all_hits, observation_date


def _negative_margin_cases(
    store: DuckDBStore,
    thresholds: RuleThresholds,
    created_at: str,
) -> tuple[list[CaseWrite], list[RuleHitWrite], str]:
    """D1 负毛利合同。"""

    result = get_negative_margin_features(store)
    cases: list[CaseWrite] = []
    all_hits: list[RuleHitWrite] = []
    observation_date = _latest_ar_date(store)

    for row in result.rows:
        customer_id = _text(row, 0)
        customer_name = _text(row, 1)
        margin_loss = _number_value(row, 2)
        if margin_loss < thresholds.negative_margin_loss:
            continue
        case_id = f"AR|{customer_id}"
        hit = _hit(
            case_id=case_id,
            rule_id="CON_NEGATIVE_MARGIN",
            rule_name="负毛利合同",
            severity="HIGH",
            exposure_amount=margin_loss,
            reason=f"负毛利合同累计亏损 {_money(margin_loss)}。",
            metrics={
                "margin_loss": margin_loss,
                _ENTITY_KEY: _entity_meta(
                    case_type="ACCOUNTS_RECEIVABLE",
                    entity_type="CUSTOMER",
                    entity_id=customer_id,
                    entity_label=f"{customer_id} {customer_name}".strip(),
                    entity_context={"customer_id": customer_id, "customer_name": customer_name},
                    observation_date=observation_date,
                    ar_balance=None,
                ),
            },
            sources=["contracts"],
            period=observation_date,
        )
        all_hits.append(hit)
        cases.append(
            CaseWrite(
                case_id=case_id,
                case_type="ACCOUNTS_RECEIVABLE",
                entity_type="CUSTOMER",
                entity_id=customer_id,
                entity_label=f"{customer_id} {customer_name}".strip(),
                entity_context={"customer_id": customer_id, "customer_name": customer_name},
                observation_date=observation_date,
                priority="MEDIUM",
                exposure_amount=margin_loss,
                summary=f"负毛利合同累计亏损 {_money(margin_loss)}。",
                rule_hit_count=1,
                rule_set_version=RULE_SET_VERSION,
                created_at=created_at,
            )
        )
    return cases, all_hits, observation_date


def _margin_optimistic_cases(
    store: DuckDBStore,
    thresholds: RuleThresholds,
    created_at: str,
) -> tuple[list[CaseWrite], list[RuleHitWrite], str]:
    """D2 实估毛利严重高估。"""

    result = get_margin_optimistic_features(store)
    cases: list[CaseWrite] = []
    all_hits: list[RuleHitWrite] = []
    observation_date = _latest_ar_date(store)

    for row in result.rows:
        contract_number = _text(row, 0)
        customer_name = _text(row, 1)
        contract_amount = _number_value(row, 2)
        weighted_est_margin = _number_value(row, 3)
        weighted_act_margin = _number_value(row, 4)
        if weighted_est_margin - weighted_act_margin < thresholds.margin_gap:
            continue
        if weighted_act_margin >= thresholds.margin_actual_max:
            continue
        case_id = f"CON|{contract_number}"
        hit = _hit(
            case_id=case_id,
            rule_id="CON_MARGIN_OPTIMISTIC",
            rule_name="实估毛利严重高估",
            severity="MEDIUM",
            exposure_amount=contract_amount,
            reason=(
                f"实估毛利率 {weighted_est_margin:.1%} 比实际净毛利率 "
                f"{weighted_act_margin:.1%} 高 "
                f"{(weighted_est_margin - weighted_act_margin):.1%}，预估过于乐观。"
            ),
            metrics={
                "contract_amount": contract_amount,
                "estimated_margin": weighted_est_margin,
                "actual_margin": weighted_act_margin,
                _ENTITY_KEY: _entity_meta(
                    case_type="ACCOUNTS_RECEIVABLE",
                    entity_type="CONTRACT",
                    entity_id=contract_number,
                    entity_label=contract_number,
                    entity_context={
                        "contract_number": contract_number,
                        "customer_name": customer_name,
                    },
                    observation_date=observation_date,
                    ar_balance=None,
                ),
            },
            sources=["contracts"],
            period=observation_date,
        )
        all_hits.append(hit)
        cases.append(
            CaseWrite(
                case_id=case_id,
                case_type="ACCOUNTS_RECEIVABLE",
                entity_type="CONTRACT",
                entity_id=contract_number,
                entity_label=contract_number,
                entity_context={"contract_number": contract_number, "customer_name": customer_name},
                observation_date=observation_date,
                priority="MEDIUM",
                exposure_amount=contract_amount,
                summary=(
                    f"合同 {contract_number} 实估毛利高估 "
                    f"{(weighted_est_margin - weighted_act_margin):.1%}。"
                ),
                rule_hit_count=1,
                rule_set_version=RULE_SET_VERSION,
                created_at=created_at,
            )
        )
    return cases, all_hits, observation_date


def _term_overage_cases(
    store: DuckDBStore,
    thresholds: RuleThresholds,
    created_at: str,
) -> tuple[list[CaseWrite], list[RuleHitWrite], str]:
    """D3 实际账期远超约定。"""

    result = get_term_overage_features(store)
    cases: list[CaseWrite] = []
    all_hits: list[RuleHitWrite] = []
    observation_date = _latest_ar_date(store)

    for row in result.rows:
        customer_id = _text(row, 0)
        customer_name = _text(row, 1)
        overage_count = int(_number_value(row, 2))
        contract_amount = _number_value(row, 3)
        max_overage = int(_number_value(row, 4))
        if contract_amount < thresholds.term_overage_amount:
            continue
        case_id = f"AR|{customer_id}"
        hit = _hit(
            case_id=case_id,
            rule_id="CON_TERM_OVERAGE",
            rule_name="实际账期远超约定",
            severity="MEDIUM",
            exposure_amount=contract_amount,
            reason=(f"{overage_count} 份合同实际账期超约定 ≥120 天，最大超期 {max_overage} 天。"),
            metrics={
                "overage_contract_count": overage_count,
                "contract_amount": contract_amount,
                "max_overage_days": max_overage,
                _ENTITY_KEY: _entity_meta(
                    case_type="ACCOUNTS_RECEIVABLE",
                    entity_type="CUSTOMER",
                    entity_id=customer_id,
                    entity_label=f"{customer_id} {customer_name}".strip(),
                    entity_context={"customer_id": customer_id, "customer_name": customer_name},
                    observation_date=observation_date,
                    ar_balance=None,
                ),
            },
            sources=["contracts"],
            period=observation_date,
        )
        all_hits.append(hit)
        cases.append(
            CaseWrite(
                case_id=case_id,
                case_type="ACCOUNTS_RECEIVABLE",
                entity_type="CUSTOMER",
                entity_id=customer_id,
                entity_label=f"{customer_id} {customer_name}".strip(),
                entity_context={"customer_id": customer_id, "customer_name": customer_name},
                observation_date=observation_date,
                priority="MEDIUM",
                exposure_amount=contract_amount,
                summary=f"{overage_count} 份合同账期超约定，最大 {max_overage} 天。",
                rule_hit_count=1,
                rule_set_version=RULE_SET_VERSION,
                created_at=created_at,
            )
        )
    return cases, all_hits, observation_date


def build_rule_scan(
    store: DuckDBStore,
    thresholds: RuleThresholds | None = None,
) -> RuleScanDraft:
    """执行确定性规则并生成可幂等保存的案件草稿。"""

    active_thresholds = thresholds or RuleThresholds()
    created_at = datetime.now(UTC).isoformat()
    _, ar_hits, ar_period = _receivable_cases(store, active_thresholds, created_at)
    _, unpaid_hits, unpaid_period = _unpaid_sales_cases(store, active_thresholds, created_at)
    _, inventory_hits, inventory_period = _inventory_cases(store, active_thresholds, created_at)
    _, zero_hits, zero_period = _zero_sales_inventory_cases(store, active_thresholds, created_at)
    _, veryold_hits, veryold_period = _very_old_inventory_cases(
        store, active_thresholds, created_at
    )
    _, extension_hits, extension_period = _extension_cases(store, active_thresholds, created_at)
    _, penalty_hits, penalty_period = _penalty_interest_cases(store, active_thresholds, created_at)
    _, staleratio_hits, staleratio_period = _stale_ratio_cases(store, active_thresholds, created_at)
    _, borrow_hits, borrow_period = _overdue_stock_cases(store, active_thresholds, created_at)
    _, return_hits, return_period = _customer_return_cases(store, active_thresholds, created_at)
    _, negpay_hits, negpay_period = _negative_payment_cases(store, active_thresholds, created_at)
    _, aging_hits, aging_period = _aging_payment_cases(store, active_thresholds, created_at)
    _, negmargin_hits, negmargin_period = _negative_margin_cases(
        store, active_thresholds, created_at
    )
    _, marginopt_hits, marginopt_period = _margin_optimistic_cases(
        store, active_thresholds, created_at
    )
    _, term_hits, term_period = _term_overage_cases(store, active_thresholds, created_at)
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
    cases, clean_hits = _merge_hits_into_cases(hits)
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
    ar_case_count = sum(1 for c in cases if c.case_type == "ACCOUNTS_RECEIVABLE")
    inv_case_count = sum(1 for c in cases if c.case_type == "INVENTORY")
    run_id = _short_id("run", f"{RULE_SET_VERSION}|{created_at}")
    return RuleScanDraft(
        run=RuleRunWrite(
            run_id=run_id,
            rule_set_version=RULE_SET_VERSION,
            observation_date=observation_date,
            cases_detected=len(cases),
            rule_hits=len(clean_hits),
            receivable_cases=ar_case_count,
            inventory_cases=inv_case_count,
            created_at=created_at,
        ),
        cases=tuple(cases),
        hits=tuple(clean_hits),
    )

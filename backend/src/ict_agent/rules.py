"""版本化风险规则、组合模式检测与案件草稿生成。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from ict_agent.data import CaseWrite, DatabaseScalar, DuckDBStore, RuleHitWrite, RuleRunWrite
from ict_agent.tools import get_inventory_rule_features, get_receivable_rule_features

RULE_SET_VERSION = "2026.08-v1"
RULE_VERSION = "1.0.0"
THRESHOLD_SOURCE = "赛事模拟数据24个月/8季度回溯后冻结的首期候选案件阈值"


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


def _priority(hits: list[RuleHitWrite]) -> str:
    order = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    return max((hit.severity for hit in hits), key=order.__getitem__)


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
    metrics: dict[str, DatabaseScalar],
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
        case_id = _short_id("case", f"AR|{customer_id}|{observation_date}|{RULE_SET_VERSION}")
        hits: list[RuleHitWrite] = []

        common_metrics: dict[str, DatabaseScalar] = {
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
        }

        if (
            overdue_60_amount >= thresholds.deep_overdue_amount
            and max_overdue_days >= thresholds.deep_overdue_days
        ):
            hits.append(
                _hit(
                    case_id=case_id,
                    rule_id="AR_DEEP_OVERDUE_MATERIAL",
                    rule_name="大额深度超期应收",
                    severity="HIGH",
                    exposure_amount=overdue_60_amount,
                    reason=(
                        f"60天以上超期 {_money(overdue_60_amount)}，最大超期 "
                        f"{max_overdue_days} 天，需要调查老账形成与回款情况。"
                    ),
                    metrics=common_metrics,
                    sources=["ar_snapshots"],
                    period=observation_date,
                )
            )

        if (
            overdue_amount >= thresholds.overdue_growth_amount
            and overdue_growth_3m >= thresholds.overdue_growth_amount
            and sales_3m > payments_3m
        ):
            hits.append(
                _hit(
                    case_id=case_id,
                    rule_id="AR_EXPOSURE_BUILDUP",
                    rule_name="应收敞口组合积累",
                    severity="HIGH",
                    exposure_amount=overdue_amount,
                    reason=(
                        f"近三个月超期增加 {_money(overdue_growth_3m)}，且新增销售 "
                        f"{_money(sales_3m)} 高于回款 {_money(payments_3m)}。"
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
                    f"命中 {len(hits)} 条调查规则。"
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
        case_id = _short_id("case", f"INV|{entity_id}|{observation_date}|{RULE_SET_VERSION}")
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
        common_metrics: dict[str, DatabaseScalar] = {
            "inventory_amount": inventory_amount,
            "previous_inventory_amount": previous_inventory_amount,
            "inventory_growth": inventory_growth,
            "inventory_growth_rate": inventory_growth_rate,
            "stale_amount": stale_amount,
            "stale_rate": stale_rate,
            "fresh_amount": fresh_amount,
            "sales_3m": sales_3m,
            "previous_sales_3m": previous_sales_3m,
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


def build_rule_scan(
    store: DuckDBStore,
    thresholds: RuleThresholds | None = None,
) -> RuleScanDraft:
    """执行确定性规则并生成可幂等保存的案件草稿。"""

    active_thresholds = thresholds or RuleThresholds()
    created_at = datetime.now(UTC).isoformat()
    ar_cases, ar_hits, ar_period = _receivable_cases(store, active_thresholds, created_at)
    inventory_cases, inventory_hits, inventory_period = _inventory_cases(
        store, active_thresholds, created_at
    )
    cases = [*ar_cases, *inventory_cases]
    hits = [*ar_hits, *inventory_hits]
    observation_date = max(ar_period, inventory_period)
    run_id = _short_id("run", f"{RULE_SET_VERSION}|{created_at}")
    return RuleScanDraft(
        run=RuleRunWrite(
            run_id=run_id,
            rule_set_version=RULE_SET_VERSION,
            observation_date=observation_date,
            cases_detected=len(cases),
            rule_hits=len(hits),
            receivable_cases=len(ar_cases),
            inventory_cases=len(inventory_cases),
            created_at=created_at,
        ),
        cases=tuple(cases),
        hits=tuple(hits),
    )

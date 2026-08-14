"""纯计算的新订单事前模拟器。

模块只消费已经准备好的客户×业务类型历史画像，不访问数据库，也不产生业务动作。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from statistics import median
from uuid import UUID, uuid5

from ict_agent.models import BusinessType, DataQualityStatus


class Scenario(StrEnum):
    RANDOM = "RANDOM"
    NORMAL = "NORMAL"
    BORDERLINE = "BORDERLINE"
    ANOMALY = "ANOMALY"


@dataclass(frozen=True)
class HistoricalOrderProfile:
    customer_id: str
    customer_name: str
    business_type: BusinessType
    positive_order_amounts: tuple[float, ...]
    gross_margin_rates: tuple[float, ...]
    payment_days: tuple[float, ...]
    source_snapshot_id: str


@dataclass(frozen=True)
class SimulatedOrder:
    simulation_id: str
    customer_id: str
    customer_name: str
    business_type: BusinessType
    amount_yuan: float
    proposed_term_days: int
    expected_margin_rate: float | None
    scenario: Scenario
    seed: int
    historical_order_count: int
    distribution_summary: dict[str, float]
    source_snapshot_id: str
    data_quality_status: DataQualityStatus
    warnings: list[str] = field(default_factory=list)
    generated_at: str = ""
    simulated: bool = True


def _percentile(values: tuple[float, ...], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize_order_amounts(values: tuple[float, ...]) -> dict[str, float]:
    """返回模拟器和Agent证据共同使用的订单金额历史分布。"""

    positive = tuple(float(value) for value in values if float(value) > 0)
    if not positive:
        raise ValueError("历史画像没有正订单金额，无法计算订单分布。")
    return {
        "p25_yuan": round(_percentile(positive, 0.25), 2),
        "median_yuan": round(_percentile(positive, 0.50), 2),
        "p75_yuan": round(_percentile(positive, 0.75), 2),
        "p90_yuan": round(_percentile(positive, 0.90), 2),
    }


def _generated_at() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def generate_simulated_order(
    profile: HistoricalOrderProfile,
    scenario: Scenario | str = Scenario.RANDOM,
    seed: int | None = None,
) -> SimulatedOrder:
    """根据历史画像生成一笔可复现的新订单情景。"""

    amounts = tuple(float(value) for value in profile.positive_order_amounts if float(value) > 0)
    if not amounts:
        raise ValueError("历史画像没有正订单金额，无法生成模拟订单。")
    selected = Scenario(scenario)
    actual_seed = seed if seed is not None else random.SystemRandom().randrange(2_147_483_648)
    rng = random.Random(actual_seed)
    if selected is Scenario.RANDOM:
        selected = rng.choices(
            (Scenario.NORMAL, Scenario.BORDERLINE, Scenario.ANOMALY),
            weights=(80, 15, 5),
            k=1,
        )[0]

    p75 = _percentile(amounts, 0.75)
    p90 = _percentile(amounts, 0.90)
    if selected is Scenario.NORMAL:
        amount = rng.choice(amounts) * rng.uniform(0.85, 1.15)
    elif selected is Scenario.BORDERLINE:
        amount = p75 * rng.uniform(1.0, 1.25)
    else:
        amount = max(p90, max(amounts)) * rng.uniform(1.5, 2.5)

    margins = tuple(float(value) for value in profile.gross_margin_rates)
    terms = tuple(float(value) for value in profile.payment_days if float(value) >= 0)
    expected_margin = median(margins) if margins else None
    proposed_term = median(terms) if terms else 0.0
    if expected_margin is not None:
        expected_margin *= rng.uniform(0.95, 1.05)
    proposed_term *= rng.uniform(0.95, 1.05)
    warnings: list[str] = []
    if len(amounts) < 5:
        warnings.append("历史正订单少于 5 笔，分布稳定性有限。")
    if not margins:
        warnings.append("历史订单缺少可计算毛利率，拟交易不生成预期毛利率。")
    if not terms:
        warnings.append("历史回款缺少有效账龄，拟账期暂按 0 天展示并要求人工补充。")
    status: DataQualityStatus = "WARNING" if warnings else "PASS"
    distribution = summarize_order_amounts(amounts)
    simulation_id = str(
        uuid5(
            UUID("00000000-0000-0000-0000-000000000001"),
            (
                f"{profile.source_snapshot_id}:{profile.customer_id}:"
                f"{profile.business_type}:{actual_seed}:{selected}"
            ),
        )
    )
    return SimulatedOrder(
        simulation_id=simulation_id,
        customer_id=profile.customer_id,
        customer_name=profile.customer_name,
        business_type=profile.business_type,
        amount_yuan=round(amount, 2),
        proposed_term_days=round(proposed_term),
        expected_margin_rate=round(expected_margin, 6) if expected_margin is not None else None,
        scenario=selected,
        seed=actual_seed,
        historical_order_count=len(amounts),
        distribution_summary=distribution,
        source_snapshot_id=profile.source_snapshot_id,
        data_quality_status=status,
        warnings=warnings,
        generated_at=_generated_at(),
    )


__all__ = [
    "HistoricalOrderProfile",
    "Scenario",
    "SimulatedOrder",
    "generate_simulated_order",
    "summarize_order_amounts",
]

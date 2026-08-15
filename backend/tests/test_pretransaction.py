from __future__ import annotations

import pytest
from ict_agent.pretransaction import (
    HistoricalOrderProfile,
    Scenario,
    generate_simulated_order,
)


@pytest.fixture
def profile() -> HistoricalOrderProfile:
    return HistoricalOrderProfile(
        customer_id="C001",
        customer_name="客户一",
        business_type="DISTRIBUTION",
        historical_order_count=8,
        distribution_summary={
            "p25_yuan": 275,
            "median_yuan": 450,
            "p75_yuan": 650,
            "p90_yuan": 860,
        },
        maximum_order_amount=1000,
        sampled_order_amount=400,
        median_gross_margin_rate=0.13,
        median_payment_days=52.5,
        source_snapshot_id="snap-1",
    )


@pytest.mark.parametrize("scenario", [Scenario.NORMAL, Scenario.BORDERLINE, Scenario.ANOMALY])
def test_explicit_scenarios(profile: HistoricalOrderProfile, scenario: Scenario) -> None:
    result = generate_simulated_order(profile, scenario, seed=7)
    assert result.scenario is scenario
    assert result.amount_yuan > 0
    assert result.simulated is True


def test_random_is_reproducible_and_has_distribution_summary(
    profile: HistoricalOrderProfile,
) -> None:
    first = generate_simulated_order(profile, seed=12)
    second = generate_simulated_order(profile, seed=12)
    assert first.simulation_id == second.simulation_id
    assert first.scenario == second.scenario
    assert first.amount_yuan == second.amount_yuan
    assert first.proposed_term_days == second.proposed_term_days
    assert first.expected_margin_rate == second.expected_margin_rate
    assert set(first.distribution_summary) == {"p25_yuan", "median_yuan", "p75_yuan", "p90_yuan"}


def test_random_can_select_all_scenarios(profile: HistoricalOrderProfile) -> None:
    scenarios = {generate_simulated_order(profile, seed=seed).scenario for seed in range(200)}
    assert scenarios == {Scenario.NORMAL, Scenario.BORDERLINE, Scenario.ANOMALY}


def test_small_history_warns() -> None:
    profile = HistoricalOrderProfile(
        "C1",
        "客户",
        "PROJECT",
        2,
        {"p25_yuan": 125, "median_yuan": 150, "p75_yuan": 175, "p90_yuan": 190},
        200,
        100,
        0.1,
        30,
        "s",
    )
    result = generate_simulated_order(profile, Scenario.NORMAL, seed=1)
    assert result.data_quality_status == "WARNING"
    assert result.warnings


def test_no_positive_orders_raise() -> None:
    profile = HistoricalOrderProfile("C1", "客户", "PROJECT", 0, {}, 0, 0, None, None, "s")
    with pytest.raises(ValueError, match="正订单金额"):
        generate_simulated_order(profile, seed=1)

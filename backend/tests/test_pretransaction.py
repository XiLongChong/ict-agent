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
        positive_order_amounts=(100, 200, 300, 400, 500, 600, 800, 1000),
        gross_margin_rates=(0.10, 0.12, 0.14, 0.16),
        payment_days=(30, 45, 60, 75),
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
    profile = HistoricalOrderProfile("C1", "客户", "PROJECT", (100, 200), (0.1,), (30,), "s")
    result = generate_simulated_order(profile, Scenario.NORMAL, seed=1)
    assert result.data_quality_status == "WARNING"
    assert result.warnings


def test_no_positive_orders_raise() -> None:
    profile = HistoricalOrderProfile("C1", "客户", "PROJECT", (-1, 0), (), (), "s")
    with pytest.raises(ValueError, match="正订单金额"):
        generate_simulated_order(profile, seed=1)

"""固定分析工具的数值口径测试。"""

import pytest
from ict_agent.data import DuckDBStore
from ict_agent.tools import (
    get_ar_trend,
    get_business_overview,
    get_customer_ar_history,
    get_customer_credit_context,
    get_customer_extension_evidence,
    get_customer_flow_history,
    get_customer_risk_profile,
    get_inventory_health,
    get_latest_ar_summary,
    get_material_inventory_age_profile,
    get_material_inventory_history,
    get_material_sales_context,
    get_project_progress,
)


def _metrics(rows: list[list[str | int | float | bool | None]]) -> dict[str, object]:
    return {str(row[0]): row[1] for row in rows}


def test_latest_ar_uses_only_latest_snapshot(store: DuckDBStore) -> None:
    result = get_latest_ar_summary(store)
    metrics = _metrics(result.rows)

    assert result.period == "2026-07-31"
    assert metrics["应收余额"] == pytest.approx(1500)
    assert metrics["超期应收"] == pytest.approx(600)
    assert metrics["超期率"] == pytest.approx(0.4)
    assert metrics["60天以上超期率"] == pytest.approx(400 / 1500)


def test_business_overview_preserves_return_values(store: DuckDBStore) -> None:
    metrics = _metrics(get_business_overview(store).rows)

    assert metrics["销售额"] == pytest.approx(290)
    assert metrics["含税粗算毛利"] == pytest.approx(67)
    assert metrics["回款额"] == pytest.approx(170)


def test_ar_trend_aggregates_each_snapshot(store: DuckDBStore) -> None:
    result = get_ar_trend(store)

    assert len(result.rows) == 2
    assert result.rows[0][1] == pytest.approx(1200)
    assert result.rows[1][1] == pytest.approx(1500)


def test_customer_profile_counts_distinct_extension_actions(store: DuckDBStore) -> None:
    result = get_customer_risk_profile(store, "C015")
    metrics = _metrics(result.rows)

    assert metrics["名单状态"] == "白名单"
    assert metrics["展期动作数"] == 2
    assert metrics["最新应收余额"] == pytest.approx(1000)
    assert metrics["最新超期率"] == pytest.approx(0.6)


def test_inventory_uses_latest_snapshot_and_non_overlapping_buckets(
    store: DuckDBStore,
) -> None:
    result = get_inventory_health(store)

    assert result.period == "2026-06-30"
    assert sum(float(row[1]) for row in result.rows) == pytest.approx(1000)
    assert {row[0] for row in result.rows} == {"0-30天", "181-365天", "365天以上"}
    assert "400.00 元" in result.summary


def test_project_progress_joins_only_formal_contract(store: DuckDBStore) -> None:
    metrics = _metrics(get_project_progress(store, "X1").rows)

    assert metrics["合同签约额"] == pytest.approx(200)
    assert metrics["项目出库额"] == pytest.approx(90)
    assert metrics["项目回款额"] == pytest.approx(70)
    assert metrics["最新应收余额"] == pytest.approx(1000)


def test_receivable_investigation_tools_keep_evidence_granular(
    store: DuckDBStore,
) -> None:
    history = get_customer_ar_history(store, "C015")
    flows = get_customer_flow_history(store, "C015")
    extensions = get_customer_extension_evidence(store, "C015")
    credit = get_customer_credit_context(store, "C015")

    assert history.rows[0][1] == pytest.approx(1000)
    assert flows.sources == ["sales", "payments", "ar_snapshots"]
    assert "当前应收中有 1 个" in extensions.summary
    assert "不能抵消" in credit.metric_definitions[0]


def test_inventory_investigation_tools_compare_age_and_sales(
    store: DuckDBStore,
) -> None:
    history = get_material_inventory_history(store, "M1", "W1")
    age = get_material_inventory_age_profile(store, "M1", "W1")
    sales = get_material_sales_context(store, "M1", "W1")

    assert history.rows[0][1] == pytest.approx(600)
    assert age.rows[0][0] == "0-30天"
    assert sales.warnings and "促销活动" in sales.warnings[0]

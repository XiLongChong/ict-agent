"""固定分析工具的数值口径测试。"""

import pytest
from ict_agent.data import DuckDBStore
from ict_agent.models import BusinessRecordSearchQuery, EvidenceQuery
from ict_agent.tools import (
    discover_evidence_capabilities,
    get_ar_trend,
    get_business_overview,
    get_customer_ar_history,
    get_customer_credit_context,
    get_customer_extension_evidence,
    get_customer_flow_history,
    get_inventory_health,
    get_latest_ar_summary,
    get_material_inventory_age_profile,
    get_material_inventory_history,
    get_material_sales_context,
    query_business_evidence,
    search_business_records,
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


def test_inventory_uses_latest_snapshot_and_non_overlapping_buckets(
    store: DuckDBStore,
) -> None:
    result = get_inventory_health(store)

    assert result.period == "2026-06-30"
    assert sum(float(row[1]) for row in result.rows) == pytest.approx(1000)
    assert {row[0] for row in result.rows} == {"0-30天", "181-365天", "365天以上"}
    assert "400.00 元" in result.summary


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


def test_receivable_catalog_exposes_live_semantics_not_sql(store: DuckDBStore) -> None:
    catalog = discover_evidence_capabilities(
        store,
        "ACCOUNTS_RECEIVABLE",
        {"customer_id": "C015", "customer_name": "测试客户"},
        "2026-07-31",
    )

    assert {(item.dataset, item.grain) for item in catalog.datasets} == {
        ("receivables", "month"),
        ("receivables", "order"),
        ("sales_payments", "month"),
        ("extensions", "order"),
        ("credit", "customer"),
        ("contracts", "contract"),
    }
    assert {item.dataset for item in catalog.datasets} == {
        "receivables",
        "sales_payments",
        "extensions",
        "credit",
        "contracts",
    }
    assert all("SQL" not in item.description for item in catalog.datasets)
    assert all(item.available for item in catalog.datasets)


def test_controlled_evidence_query_projects_metrics_and_scopes_customer(
    store: DuckDBStore,
) -> None:
    result = query_business_evidence(
        store,
        "ACCOUNTS_RECEIVABLE",
        {"customer_id": "C015"},
        EvidenceQuery(
            dataset="receivables",
            grain="month",
            metrics=["ar_amount", "overdue_amount", "overdue_30_amount"],
            time_window="last_3_months",
            limit=3,
        ),
    )

    assert result.columns == ["期间", "应收金额_元", "超期应收_元", "30天以上超期_元"]
    assert result.rows[0][1:] == pytest.approx([1000, 600, 500])
    assert len(result.rows) == 2


def test_controlled_evidence_query_rejects_invalid_dataset_grain(
    store: DuckDBStore,
) -> None:
    with pytest.raises(ValueError, match="不支持数据集 credit/month"):
        query_business_evidence(
            store,
            "ACCOUNTS_RECEIVABLE",
            {"customer_id": "C015"},
            EvidenceQuery(
                dataset="credit",
                grain="month",
                metrics=["credit_limit"],
                time_window="latest",
            ),
        )


def test_inventory_investigation_tools_compare_age_and_sales(
    store: DuckDBStore,
) -> None:
    history = get_material_inventory_history(store, "M1", "W1")
    age = get_material_inventory_age_profile(store, "M1", "W1")
    sales = get_material_sales_context(store, "M1", "W1")

    assert history.rows[0][1] == pytest.approx(600)
    assert age.rows[0][0] == "0-30天"
    assert sales.warnings and "促销活动" in sales.warnings[0]


def test_inventory_uses_same_controlled_query_contract(store: DuckDBStore) -> None:
    result = query_business_evidence(
        store,
        "INVENTORY",
        {"material_code": "M1", "inventory_org": "W1"},
        EvidenceQuery(
            dataset="inventory",
            grain="quarter",
            metrics=["inventory_amount", "stale_inventory_amount"],
            time_window="last_6_months",
            limit=2,
        ),
    )

    assert result.columns == ["期间", "库存金额_元", "180天以上库存_元"]
    assert len(result.rows) == 1


def test_business_record_search_is_scoped_to_current_case(store: DuckDBStore) -> None:
    result = search_business_records(
        store,
        "ACCOUNTS_RECEIVABLE",
        {"customer_id": "C015", "customer_name": "测试客户"},
        BusinessRecordSearchQuery(record_type="order", query="S1", limit=10),
    )

    assert result.rows == [["order", "S1", "S1"]]
    assert result.sources == ["ar_snapshots"]

"""调查证据策略、查询完整度和案件数据身份测试。"""

import duckdb
import pytest
from ict_agent.data import DuckDBStore
from ict_agent.evidence_policy import requirements_for, supported_signal_codes
from ict_agent.models import (
    EvidenceQuery,
    InvestigationCaseInput,
    InvestigationDataQuality,
    InvestigationSignalInput,
)
from ict_agent.tools import (
    AnalysisInputError,
    discover_evidence_capabilities,
    query_business_evidence,
    validate_investigation_context,
)

DETERMINISTIC_SIGNAL_CODES = {
    "AR_OPERATING_DEEP_OVERDUE",
    "AR_OPERATING_EXPOSURE_BUILDUP",
    "AR_BLACKLIST_EXPOSURE",
    "AR_OVERDUE_RATE_HIGH",
    "AR_UNPAID_AGING",
    "AR_OVER_CREDIT_LIMIT",
    "AR_NO_CREDIT_WITH_EXPOSURE",
    "AR_EXTENSION_ABUSE",
    "AR_PENALTY_INTEREST_HIGH",
    "SLS_RETURN_ABNORMAL",
    "PAY_OFFSET_ABNORMAL",
    "PAY_AGING_OVER_365",
    "INV_MATERIAL_BUILDUP",
    "INV_STALE_NO_SALES",
    "INV_BUILDUP_SALES_SLOWDOWN",
    "INV_ZERO_SALES_STOCK",
    "INV_STALE_RATIO_HIGH",
    "INV_VERY_OLD_STOCK",
    "INV_OVERDUE_STOCK",
    "CON_NEGATIVE_MARGIN",
    "CON_MARGIN_OPTIMISTIC",
    "CON_TERM_OVERAGE",
    "CREDIT_EXPOSURE_DECLINE",
}


def _signal(code: str, period: str = "2026-07-31") -> InvestigationSignalInput:
    return InvestigationSignalInput(
        signal_id=f"signal-{code}",
        signal_code=code,
        signal_name=code,
        source_version="2.0.0",
        severity="HIGH",
        exposure_amount=100,
        reason="test",
        metrics={},
        threshold_source="test",
        threshold_version="2.0.0",
        sources=["ar_snapshots"],
        period=period,
    )


def test_all_deterministic_signals_have_explicit_evidence_policy() -> None:
    assert supported_signal_codes() >= DETERMINISTIC_SIGNAL_CODES
    assert len(DETERMINISTIC_SIGNAL_CODES) == 23


def test_multi_signal_policy_unions_datasets_metrics_and_windows() -> None:
    requirements = requirements_for(
        "RECEIVABLES",
        {"SLS_RETURN_ABNORMAL", "PAY_OFFSET_ABNORMAL", "CON_TERM_OVERAGE"},
    )
    by_key = {(item.dataset, item.grain): item for item in requirements}

    assert {"sales_returns", "payments", "contracts"} <= {item.dataset for item in requirements}
    assert by_key[("sales_returns", "customer")].metrics >= {
        "gross_sales_amount",
        "return_amount",
        "return_ratio",
    }
    assert by_key[("contracts", "contract")].minimum_time_window == "all"


def test_evidence_query_reports_total_returned_and_truncated(store: DuckDBStore) -> None:
    result = query_business_evidence(
        store,
        "RECEIVABLES",
        {"customer_id": "C015"},
        EvidenceQuery(
            dataset="sales_returns",
            grain="order",
            metrics=["gross_sales_amount", "return_amount", "return_ratio"],
            time_window="all",
            limit=1,
        ),
    )

    assert result.total_rows == 2
    assert result.returned_rows == 1
    assert result.is_truncated is True


def test_signal_specific_queries_expose_frozen_metrics(store: DuckDBStore) -> None:
    contract = query_business_evidence(
        store,
        "RECEIVABLES",
        {"customer_id": "C015"},
        EvidenceQuery(
            dataset="contracts",
            grain="contract",
            metrics=[
                "estimated_margin_rate",
                "actual_margin_rate",
                "margin_gap",
                "contract_term_days",
                "actual_term_days",
                "term_overage_days",
            ],
            time_window="all",
            limit=10,
        ),
    )
    overdue_inventory = query_business_evidence(
        store,
        "INVENTORY",
        {"material_code": "M3", "inventory_org": "W1"},
        EvidenceQuery(
            dataset="inventory",
            grain="inventory_record",
            metrics=[
                "inventory_amount",
                "inventory_quantity",
                "max_inventory_overdue_days",
                "overdue_inventory_rows",
            ],
            time_window="latest",
            limit=10,
        ),
    )

    assert contract.rows[0][2:] == pytest.approx([0.2, 0.1, 0.1, 60, 80, 20])
    assert overdue_inventory.rows[0][2:] == pytest.approx([100, 1, 120, 1])


def test_payment_and_collection_queries_reproduce_rule_evidence(
    store: DuckDBStore,
) -> None:
    with duckdb.connect(str(store.database_path)) as connection:
        connection.execute(
            """
            INSERT INTO payments (
                "回款日期", "客户编号", "合同号", "销售订单号", "回款金额",
                "超期利息金额", "是否超期", "超期天数", "回款账龄"
            ) VALUES ('2026-07-25', 'C015', 'X1', 'S1', -10, 2, 'Y', 20, 400)
            """
        )
        connection.execute(
            """
            INSERT INTO sales (
                "出库日期", "客户编号", "客户名称", "合同号", "销售订单号",
                "销售金额_折扣后_含税"
            ) VALUES ('2026-07-01', 'C015', '南京沛图商贸有限公司', 'X1', 'S4', 50)
            """
        )

    payment = query_business_evidence(
        store,
        "RECEIVABLES",
        {"customer_id": "C015"},
        EvidenceQuery(
            dataset="payments",
            grain="customer",
            metrics=[
                "payment_amount",
                "positive_payment_amount",
                "negative_payment_amount",
                "negative_payment_ratio",
                "over_365_payment_amount",
                "max_payment_age_days",
            ],
            time_window="all",
            limit=10,
        ),
    )
    collection = query_business_evidence(
        store,
        "RECEIVABLES",
        {"customer_id": "C015"},
        EvidenceQuery(
            dataset="collections",
            grain="customer",
            metrics=["sales_amount", "payment_amount", "unpaid_amount", "max_unpaid_days"],
            time_window="all",
            limit=10,
        ),
    )

    assert payment.rows[0][1:6] == pytest.approx([60, 70, 10, 1 / 6, -10])
    assert payment.rows[0][-1] == 400
    assert payment.total_rows == payment.returned_rows == 1
    assert payment.is_truncated is False
    assert collection.rows[0][1:] == pytest.approx([150, 70, 50, 30])


def test_high_cardinality_details_are_counted_before_sql_limit(
    store: DuckDBStore,
) -> None:
    with duckdb.connect(str(store.database_path)) as connection:
        connection.execute(
            """
            INSERT INTO sales (
                "出库日期", "客户编号", "客户名称", "合同号", "销售订单号",
                "销售金额_折扣后_含税"
            )
            SELECT DATE '2026-07-01', 'C015', '南京沛图商贸有限公司', 'X1',
                   'BULK-S' || CAST(i AS VARCHAR), CAST(i + 1 AS DOUBLE)
            FROM range(250) AS generated(i)
            """
        )
        connection.execute(
            """
            INSERT INTO payments (
                "回款日期", "客户编号", "合同号", "销售订单号", "回款金额", "回款账龄"
            )
            SELECT DATE '2026-07-15', 'C015', 'X1',
                   'BULK-S' || CAST(i AS VARCHAR), CAST(i + 1 AS DOUBLE), 30
            FROM range(250) AS generated(i)
            """
        )

    context = {"customer_id": "C015"}
    catalog = discover_evidence_capabilities(
        store,
        "RECEIVABLES",
        context,
        "2026-07-31",
    )
    catalog_by_key = {(item.dataset, item.grain): item for item in catalog.datasets}
    returns = query_business_evidence(
        store,
        "RECEIVABLES",
        context,
        EvidenceQuery(
            dataset="sales_returns",
            grain="order",
            metrics=["gross_sales_amount", "return_amount", "return_ratio"],
            time_window="all",
            sort_by="gross_sales_amount",
            sort_direction="desc",
            limit=3,
        ),
    )

    assert catalog_by_key[("sales_returns", "order")].total_rows == 252
    assert catalog_by_key[("sales_returns", "order")].returned_rows == 1
    assert catalog_by_key[("sales_returns", "order")].is_truncated is True
    assert catalog_by_key[("payments", "order")].total_rows == 251
    assert catalog_by_key[("collections", "order")].total_rows == 251
    assert returns.total_rows == 252
    assert returns.returned_rows == 3
    assert returns.is_truncated is True
    assert returns.rows[0][4] == 250


def test_fixed_snapshot_and_rule_observation_date_are_validated(store: DuckDBStore) -> None:
    case = InvestigationCaseInput(
        case_id="AR|C015",
        source="RULE_SCAN",
        subject_type="CUSTOMER",
        subject_id="C015",
        subject_label="C015",
        subject_context={"customer_id": "C015"},
        investigation_profile="RECEIVABLES",
        observation_date="2026-07-31",
        priority="HIGH",
        exposure_amount=100,
        summary="test",
        source_set_version="2026.08-v2",
        source_snapshot_id=store.get_snapshot().snapshot_id,
        signals=[_signal("AR_OVERDUE_RATE_HIGH")],
        data_quality=InvestigationDataQuality(status="PASS"),
    )

    validate_investigation_context(store, case)
    with pytest.raises(AnalysisInputError, match="数据快照"):
        validate_investigation_context(
            store, case.model_copy(update={"source_snapshot_id": "wrong-snapshot"})
        )
    with pytest.raises(AnalysisInputError, match="观察日期"):
        validate_investigation_context(
            store,
            case.model_copy(
                update={
                    "observation_date": "2026-06-30",
                    "signals": [_signal("AR_OVERDUE_RATE_HIGH", "2026-06-30")],
                }
            ),
        )


def test_simulated_order_uses_generation_date_not_csv_observation_date(
    store: DuckDBStore,
) -> None:
    generated_date = "2026-08-15"
    case = InvestigationCaseInput(
        case_id="pre-test",
        source="PRE_TRANSACTION_SIMULATION",
        subject_type="CUSTOMER",
        subject_id="C015",
        subject_label="C015",
        subject_context={
            "customer_id": "C015",
            "simulation_id": "sim-test",
            "generated_at": f"{generated_date}T10:00:00+08:00",
            "simulated": True,
        },
        investigation_profile="PRE_TRANSACTION",
        business_type="DISTRIBUTION",
        observation_date=generated_date,
        priority="MEDIUM",
        exposure_amount=100,
        summary="test",
        source_set_version="pre-transaction-simulator-1.0",
        source_snapshot_id=store.get_snapshot().snapshot_id,
        signals=[_signal("PRE_TRANSACTION_REVIEW", generated_date)],
        data_quality=InvestigationDataQuality(status="PASS"),
    )

    validate_investigation_context(store, case)

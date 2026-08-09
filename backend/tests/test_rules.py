"""风险规则回溯、组合命中与案件库幂等测试。"""

from pathlib import Path

from ict_agent.data import CaseStore, DuckDBStore, ReviewWrite
from ict_agent.rules import RuleThresholds, build_rule_scan


def _test_thresholds() -> RuleThresholds:
    return RuleThresholds(
        deep_overdue_amount=100,
        deep_overdue_days=90,
        overdue_growth_amount=50,
        stale_inventory_amount=100,
        stale_inventory_rate=0.3,
        inventory_buildup_amount=500,
        inventory_buildup_rate=0.5,
        inventory_slowdown_amount=500,
        inventory_sales_decline_rate=0.5,
    )


def test_rule_scan_detects_single_and_composite_risks(store: DuckDBStore) -> None:
    draft = build_rule_scan(store, _test_thresholds())
    rule_ids = {hit.rule_id for hit in draft.hits}

    assert draft.run.observation_date == "2026-07-31"
    assert draft.run.receivable_cases == 1
    assert draft.run.inventory_cases == 3
    assert "AR_OPERATING_DEEP_OVERDUE" in rule_ids
    assert "AR_OPERATING_EXPOSURE_BUILDUP" in rule_ids
    assert "AR_BLACKLIST_EXPOSURE" not in rule_ids
    assert "INV_MATERIAL_BUILDUP" in rule_ids
    assert "INV_STALE_NO_SALES" in rule_ids


def test_case_store_preserves_idempotency_and_review(
    store: DuckDBStore,
    tmp_path: Path,
) -> None:
    draft = build_rule_scan(store, _test_thresholds())
    case_store = CaseStore(tmp_path / "cases.duckdb")

    first_created = case_store.save_rule_scan(draft.run, draft.cases, draft.hits)
    second_draft = build_rule_scan(store, _test_thresholds())
    second_created = case_store.save_rule_scan(
        second_draft.run, second_draft.cases, second_draft.hits
    )

    assert first_created == 4
    assert second_created == 0
    assert len(case_store.fetch_cases().rows) == 4

    case_id = str(case_store.fetch_cases().rows[0][0])
    case_store.save_review(
        ReviewWrite(
            review_id="review-1",
            case_id=case_id,
            decision="MONITOR",
            reviewer="测试审核人",
            reason="持续回款，七日后复查。",
            action="跟踪回款",
            next_review_at="2026-08-15",
            created_at="2026-08-08T00:00:00+00:00",
        ),
        "MONITORING",
    )

    assert case_store.fetch_case(case_id).rows[0][7] == "MONITORING"
    assert case_store.fetch_reviews(case_id).rows[0][2] == "MONITOR"

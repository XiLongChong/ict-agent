"""调查 Agent 评测机的硬门槛、分项评分和版本对比测试。"""

from ict_agent.evaluation import (
    apply_human_reviews,
    compare_evaluation_runs,
    evaluate_investigation_run,
    replace_evaluation_runs,
)
from ict_agent.models import Evidence, InvestigationReport


def _report() -> InvestigationReport:
    return InvestigationReport.model_validate(
        {
            "executive_summary": "库存增长与近期销售放缓构成早期风险信号，建议核对补货依据。",
            "risk_assessment": {
                "stage": "EARLY_WARNING",
                "statement": "库存和销售证据共同支持早期预警。",
                "evidence_ids": ["ev-history", "ev-sales"],
                "drivers": ["库存增加。", "近期销售下降。"],
                "counter_signals": [],
                "management_posture": "建议人工核对补货计划并持续监测销售。",
                "watch_items": ["后续销售能否消化当前库存。"],
            },
            "possibility_assessments": [
                {
                    "assessment_id": "P1",
                    "possibility": "补货超过近期销售消化能力可能是库存增长原因。",
                    "likelihood": {"lower_percent": 40, "upper_percent": 70},
                    "rationale": "库存增加与销售下降方向一致，但缺少补货计划。",
                    "supporting_evidence_ids": ["ev-history"],
                    "contradicting_evidence_ids": [],
                    "missing_evidence": ["促销和补货计划"],
                    "business_implication": "需要核对补货计划后决定是否调整库存。",
                }
            ],
            "facts": [
                {"statement": "库存增加。", "evidence_ids": ["ev-history"]},
                {"statement": "近期销售下降。", "evidence_ids": ["ev-sales"]},
            ],
            "limitations": ["没有促销和补货计划，不能证明具体原因。"],
            "recommended_priority": "HIGH",
            "recommended_actions": [
                {
                    "owner": "库存管理人员",
                    "action": "人工核对补货计划并持续监测销售。",
                    "urgency": "SHORT_TERM",
                    "rationale": "库存增加与销售下降同时出现。",
                    "completion_evidence": "补货计划核对记录。",
                }
            ],
            "requires_human_review": True,
        }
    )


def _evidence() -> list[Evidence]:
    return [
        Evidence(
            evidence_id="ev-history",
            tool_name="get_evidence",
            arguments={"dataset": "inventory", "grain": "quarter"},
            sources=["inventory_snapshots"],
            period="2026-Q2",
            summary="库存季度历史。",
            columns=["period", "amount"],
            rows=[["2026-Q2", 100]],
            metric_definitions=["库存金额按单一期末快照聚合。"],
        ),
        Evidence(
            evidence_id="ev-sales",
            tool_name="get_evidence",
            arguments={"dataset": "sales", "grain": "month"},
            sources=["sales"],
            period="2026-04 至 2026-06",
            summary="最近三个月销售。",
            columns=["month", "amount"],
            rows=[["2026-06", 0]],
            metric_definitions=["销售退货保留负数。"],
        ),
    ]


def _spec() -> dict[str, object]:
    return {
        "expectations": {
            "required_evidence": [
                {"dataset": "inventory", "grain": "quarter"},
                {"dataset": "sales", "grain": "month"},
            ],
            "maximum_evidence_calls": 4,
            "allowed_stages": ["EARLY_WARNING"],
            "allowed_priorities": ["HIGH"],
        },
        "human_review": ["是否区分风险模式与具体原因"],
    }


def test_complete_grounded_run_reaches_full_score() -> None:
    evaluation = evaluate_investigation_run(
        _spec(),
        report=_report(),
        evidence=_evidence(),
        called_tools=["inspect_data", "get_evidence"],
        partial=False,
        error=None,
        duration_seconds=1.2345,
        usage={"total_tokens": 100},
        forbidden_claims=["促销导致"],
    )

    assert evaluation["score"] == 100
    assert evaluation["automatic_pass"] is True
    assert all(evaluation["hard_gates"].values())
    assert evaluation["human_review"]["status"] == "PENDING"


def test_missing_evidence_and_unqualified_claim_fail_hard_gates() -> None:
    report = _report().model_copy(update={"executive_summary": "促销导致库存增长。"})
    evaluation = evaluate_investigation_run(
        _spec(),
        report=report,
        evidence=_evidence()[:1],
        called_tools=[],
        partial=False,
        error=None,
        duration_seconds=1,
        usage=None,
        forbidden_claims=["促销导致"],
    )

    assert evaluation["automatic_pass"] is False
    assert evaluation["hard_gates"]["required_evidence_coverage"] is False
    assert evaluation["hard_gates"]["citation_integrity"] is False
    assert evaluation["hard_gates"]["no_unqualified_forbidden_claims"] is False


def test_semantic_duplicate_and_conflicting_possibility_reference_are_detected() -> None:
    report = _report()
    report.possibility_assessments[0].contradicting_evidence_ids = ["ev-history"]
    duplicate = _evidence()[0].model_copy(update={"evidence_id": "ev-history-duplicate"})
    evaluation = evaluate_investigation_run(
        _spec(),
        report=report,
        evidence=[*_evidence(), duplicate],
        called_tools=["inspect_data", "get_evidence"],
        partial=False,
        error=None,
        duration_seconds=1,
        usage=None,
        forbidden_claims=[],
    )

    assert evaluation["score_breakdown"]["investigation_strategy"]["duplicate_queries"] == 1
    assert evaluation["hard_gates"]["possibility_estimate_integrity"] is False


def test_broader_time_window_evidence_counts_as_duplicate() -> None:
    broad = _evidence()[1].model_copy(
        update={
            "arguments": {
                "dataset": "sales",
                "grain": "month",
                "metrics": ["sales_amount", "net_quantity", "gross_profit"],
                "time_window": "all",
                "limit": 30,
            }
        }
    )
    narrower = broad.model_copy(
        update={
            "evidence_id": "ev-sales-subset",
            "arguments": {
                "dataset": "sales",
                "grain": "month",
                "metrics": ["sales_amount", "net_quantity"],
                "time_window": "last_6_months",
                "limit": 30,
            },
        }
    )
    evaluation = evaluate_investigation_run(
        _spec(),
        report=_report(),
        evidence=[_evidence()[0], broad, narrower],
        called_tools=["inspect_data", "get_evidence"],
        partial=False,
        error=None,
        duration_seconds=1,
        usage=None,
        forbidden_claims=[],
    )

    assert evaluation["score_breakdown"]["investigation_strategy"]["duplicate_queries"] == 1


def test_explicit_negation_is_not_treated_as_forbidden_claim() -> None:
    report = _report().model_copy(
        update={"executive_summary": "大额应收不等于已确认坏账，不能断言已无法回收。"}
    )
    evaluation = evaluate_investigation_run(
        _spec(),
        report=report,
        evidence=_evidence(),
        called_tools=["inspect_data", "get_evidence"],
        partial=False,
        error=None,
        duration_seconds=1,
        usage=None,
        forbidden_claims=["已确认坏账", "已无法回收"],
    )

    assert evaluation["hard_gates"]["no_unqualified_forbidden_claims"] is True


def test_run_comparison_uses_matching_eval_and_repeat() -> None:
    baseline = {
        "run_id": "baseline",
        "summary": {"mean_score": 80, "automatic_pass_rate": 0.5},
        "runs": [
            {
                "eval_id": "E1",
                "repeat": 1,
                "evaluation": {
                    "score": 80,
                    "automatic_pass": False,
                    "diagnostics": {"evidence_calls": 5},
                    "runtime": {"duration_seconds": 10, "usage": {"total_tokens": 100}},
                },
            },
            {
                "eval_id": "E2",
                "repeat": 1,
                "evaluation": {
                    "score": 80,
                    "automatic_pass": False,
                    "diagnostics": {"evidence_calls": 5},
                    "runtime": {"duration_seconds": 10, "usage": None},
                },
            },
        ],
    }
    candidate = {
        "run_id": "candidate",
        "summary": {"mean_score": 90, "automatic_pass_rate": 1},
        "runs": [
            {
                "eval_id": "E1",
                "repeat": 1,
                "evaluation": {
                    "score": 90,
                    "automatic_pass": True,
                    "diagnostics": {"evidence_calls": 3},
                    "runtime": {"duration_seconds": 8, "usage": {"total_tokens": 70}},
                },
            },
            {
                "eval_id": "E2",
                "repeat": 1,
                "evaluation": {
                    "score": 90,
                    "automatic_pass": True,
                    "diagnostics": {"evidence_calls": 3},
                    "runtime": {"duration_seconds": 8, "usage": {"total_tokens": 60}},
                },
            },
        ],
    }

    comparison = compare_evaluation_runs(baseline, candidate)

    assert comparison["matched_runs"] == 2
    assert comparison["summary"]["mean_score_delta"] == 10
    assert comparison["summary"]["duration_delta_rate"] == -0.2
    assert comparison["summary"]["total_tokens_delta_rate"] == -0.3
    assert comparison["summary"]["token_comparable_runs"] == 1
    assert comparison["summary"]["evidence_calls_delta"] == -4
    assert comparison["cases"][0]["score_delta"] == 10


def test_human_review_is_required_for_release_pass() -> None:
    run = {
        "run_id": "candidate",
        "summary": {},
        "runs": [
            {
                "eval_id": "E1",
                "repeat": 1,
                "evaluation": {
                    "automatic_pass": True,
                    "human_review": {
                        "status": "PENDING",
                        "criteria": [{"question": "是否有依据", "decision": "PENDING", "note": ""}],
                    },
                },
            }
        ],
    }
    reviews = {
        "run_id": "candidate",
        "reviewer": "reviewer",
        "reviewed_at": "2026-08-10T00:00:00+08:00",
        "reviews": [
            {
                "eval_id": "E1",
                "repeat": 1,
                "criteria": [{"question": "是否有依据", "decision": "PASS", "note": "已核对"}],
            }
        ],
    }

    reviewed = apply_human_reviews(run, reviews)

    assert reviewed["runs"][0]["evaluation"]["release_pass"] is True
    assert reviewed["summary"]["release_pass_rate"] == 1


def test_targeted_rerun_replaces_only_matching_runs() -> None:
    common = {
        "scope": "INVESTIGATION_AGENT_ONLY",
        "dataset_version": "3.0",
        "dataset_sha256": "dataset",
        "model": "model",
        "data_snapshot": {
            "snapshot_id": "snapshot",
            "schema_fingerprint": "schema",
            "database_sha256": "database",
        },
    }
    base = {
        **common,
        "run_id": "base",
        "runs": [
            {"eval_id": "E1", "repeat": 1, "value": "old"},
            {"eval_id": "E2", "repeat": 1, "value": "keep"},
        ],
    }
    replacement = {
        **common,
        "run_id": "replacement",
        "runs": [{"eval_id": "E1", "repeat": 1, "value": "new"}],
    }

    merged = replace_evaluation_runs(base, replacement)

    assert merged["source_run_ids"] == ["base", "replacement"]
    assert [item["value"] for item in merged["runs"]] == ["new", "keep"]

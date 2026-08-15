"""调查 Agent 的确定性评测与跨版本对比，不包含规则引擎评测。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from ict_agent.models import Evidence, InvestigationReport
from ict_agent.semantic import time_window_covers

ABSTENTION_MARKERS = (
    "无法判断",
    "不能判断",
    "没有证据",
    "证据不足",
    "尚无数据",
    "不能证明",
    "仍需补证",
    "不等于",
    "不能断言",
    "不得断言",
    "未确认",
)

_HISTORICAL_EVIDENCE_KEYS = {
    "inspect_inventory_history": ("inventory", "quarter"),
    "inspect_inventory_age_profile": ("inventory", "age_bucket"),
    "inspect_material_sales": ("sales", "month"),
}


def logical_evidence_key(evidence: Evidence) -> tuple[str, str] | None:
    """把统一查询证据映射到稳定的业务证据组。"""

    dataset = evidence.arguments.get("dataset")
    grain = evidence.arguments.get("grain")
    if isinstance(dataset, str) and isinstance(grain, str):
        return dataset, grain
    return _HISTORICAL_EVIDENCE_KEYS.get(str(evidence.tool_name))


def _report_sentences(report: InvestigationReport) -> list[str]:
    parts = [
        report.executive_summary,
        report.risk_assessment.statement,
        *report.risk_assessment.drivers,
        *report.risk_assessment.counter_signals,
        *(fact.statement for fact in report.facts),
        *(item.possibility for item in report.possibility_assessments),
        *(item.rationale for item in report.possibility_assessments),
        *(item.statement for item in report.data_conflicts),
    ]
    return [item.strip() for item in re.split(r"[。！？;；\n]", "。".join(parts)) if item.strip()]


def _report_references(report: InvestigationReport) -> set[str]:
    references = set(report.risk_assessment.evidence_ids)
    for fact in report.facts:
        references.update(fact.evidence_ids)
    for item in report.possibility_assessments:
        references.update(item.supporting_evidence_ids)
        references.update(item.contradicting_evidence_ids)
    for conflict in report.data_conflicts:
        references.update(conflict.evidence_ids)
    return references


def _possibility_errors(report: InvestigationReport) -> list[str]:
    errors: list[str] = []
    for item in report.possibility_assessments:
        overlap = set(item.supporting_evidence_ids) & set(item.contradicting_evidence_ids)
        if overlap:
            errors.append(f"{item.assessment_id}: 同一证据同时支持和反驳 {sorted(overlap)}")
        width = item.likelihood.upper_percent - item.likelihood.lower_percent
        if item.missing_evidence and width < 20:
            errors.append(f"{item.assessment_id}: 缺少关键证据但概率区间过窄")
    return errors


def _duplicate_evidence_queries(evidence: Sequence[Evidence]) -> int:
    previous_queries: list[tuple[tuple[object, ...], object, set[object], int]] = []
    duplicates = 0
    for item in evidence:
        key = logical_evidence_key(item)
        if key is None:
            continue
        time_window = item.arguments.get("time_window")
        scope: tuple[object, ...] = (
            key,
            item.arguments.get("sort_by"),
            item.arguments.get("sort_direction"),
        )
        metrics_value = item.arguments.get("metrics")
        metrics: set[object] = set(metrics_value) if isinstance(metrics_value, list) else set()
        limit_value = item.arguments.get("limit")
        limit = limit_value if isinstance(limit_value, int) else 0
        if any(
            previous_scope == scope
            and time_window_covers(previous_window, time_window)
            and previous_metrics >= metrics
            and previous_limit >= limit
            for previous_scope, previous_window, previous_metrics, previous_limit in (
                previous_queries
            )
        ):
            duplicates += 1
        previous_queries.append((scope, time_window, metrics, limit))
    return duplicates


def _ratio_score(passed: int, total: int, maximum: int) -> int:
    if total == 0:
        return maximum
    return round(maximum * passed / total)


def evaluate_investigation_run(
    spec: dict[str, Any],
    *,
    report: InvestigationReport | None,
    evidence: Sequence[Evidence],
    called_tools: Sequence[str],
    partial: bool,
    error: str | None,
    duration_seconds: float,
    usage: dict[str, int | float | str | None] | None,
    forbidden_claims: Sequence[str],
) -> dict[str, Any]:
    """给单次调查打 100 分，并保留不允许被总分掩盖的硬门槛。"""

    expectations = spec["expectations"]
    required_keys = {
        (item["dataset"], item["grain"]) for item in expectations.get("required_evidence", [])
    }
    actual_keys = {key for item in evidence if (key := logical_evidence_key(item))}
    missing_keys = sorted(required_keys - actual_keys)
    maximum_evidence_calls = int(expectations.get("maximum_evidence_calls", 9))
    duplicates = _duplicate_evidence_queries(evidence)
    called_tool_set = set(called_tools)
    discovery_used = "inspect_data" in called_tool_set

    execution_score = 0 if error else (4 if partial else 10)
    coverage_score = _ratio_score(len(required_keys & actual_keys), len(required_keys), 12)
    strategy_score = (
        (4 if discovery_used else 0)
        + coverage_score
        + (4 if len(evidence) <= maximum_evidence_calls and duplicates == 0 else 0)
    )

    source_score = _ratio_score(sum(bool(item.sources) for item in evidence), len(evidence), 5)
    period_score = _ratio_score(sum(bool(item.period) for item in evidence), len(evidence), 3)
    content_score = _ratio_score(
        sum(bool(item.rows or item.summary) for item in evidence), len(evidence), 4
    )
    definition_score = _ratio_score(
        sum(bool(item.metric_definitions) for item in evidence), len(evidence), 4
    )
    variety_target = min(max(len(required_keys), 1), 4)
    variety_score = _ratio_score(min(len(actual_keys), variety_target), variety_target, 4)
    evidence_quality_score = (
        source_score + period_score + content_score + definition_score + variety_score
    )

    valid_ids = {item.evidence_id for item in evidence}
    evidence_by_id = {item.evidence_id: item for item in evidence}
    references = _report_references(report) if report else set()
    bad_references = sorted(references - valid_ids)
    facts_cited = bool(report and report.facts)
    if report is not None:
        facts_cited = facts_cited and all(fact.evidence_ids for fact in report.facts)
    risk_cited = bool(report and report.risk_assessment.evidence_ids)
    possibility_errors = _possibility_errors(report) if report else ["没有报告"]
    risk_reference_keys = {
        key
        for evidence_id in (report.risk_assessment.evidence_ids if report else [])
        if (item := evidence_by_id.get(evidence_id))
        if (key := logical_evidence_key(item))
    }
    risk_variety_target = min(max(len(required_keys), 1), 2)
    citation_score = (
        (10 if report and not bad_references else 0)
        + (5 if facts_cited else 0)
        + (5 if risk_cited else 0)
        + (5 if report and not possibility_errors else 0)
        + _ratio_score(min(len(risk_reference_keys), risk_variety_target), risk_variety_target, 5)
    )

    claim_violations: list[dict[str, str]] = []
    if report:
        for sentence in _report_sentences(report):
            for claim in forbidden_claims:
                if claim in sentence and not any(
                    marker in sentence for marker in ABSTENTION_MARKERS
                ):
                    claim_violations.append({"claim": claim, "sentence": sentence})
    allowed_stages = set(expectations.get("allowed_stages", []))
    stage_allowed = report is not None and (
        not allowed_stages or report.risk_assessment.stage in allowed_stages
    )
    uncertainty_scoped = bool(
        report
        and (
            report.limitations
            or any(item.missing_evidence for item in report.possibility_assessments)
        )
    )
    boundary_score = (
        (6 if report and not claim_violations else 0)
        + (2 if uncertainty_scoped else 0)
        + (2 if stage_allowed else 0)
    )

    allowed_priorities = set(expectations.get("allowed_priorities", []))
    priority_allowed = report is not None and (
        not allowed_priorities or report.recommended_priority in allowed_priorities
    )
    handoff_score = (
        (2 if report and report.requires_human_review else 0)
        + (3 if report and report.recommended_actions else 0)
        + (3 if report and report.risk_assessment.watch_items else 0)
        + (2 if priority_allowed else 0)
    )

    score = (
        execution_score
        + strategy_score
        + evidence_quality_score
        + citation_score
        + boundary_score
        + handoff_score
    )
    hard_gates = {
        "run_completed": report is not None and error is None and not partial,
        "required_evidence_coverage": not missing_keys,
        "citation_integrity": report is not None and not bad_references and facts_cited,
        "possibility_estimate_integrity": report is not None and not possibility_errors,
        "no_unqualified_forbidden_claims": report is not None and not claim_violations,
        "actionable_stage": report is not None
        and report.risk_assessment.stage != "LIMITED"
        and stage_allowed,
        "human_review_required": report is not None and report.requires_human_review,
    }
    automatic_pass = all(hard_gates.values()) and score >= 80

    return {
        "automatic_pass": automatic_pass,
        "score": score,
        "score_threshold": 80,
        "hard_gates": hard_gates,
        "score_breakdown": {
            "execution": {"score": execution_score, "maximum": 10},
            "investigation_strategy": {
                "score": strategy_score,
                "maximum": 20,
                "discovery_used": discovery_used,
                "evidence_coverage_score": coverage_score,
                "duplicate_queries": duplicates,
            },
            "evidence_quality": {
                "score": evidence_quality_score,
                "maximum": 20,
            },
            "citation_and_reasoning": {"score": citation_score, "maximum": 30},
            "conclusion_boundaries": {"score": boundary_score, "maximum": 10},
            "human_handoff": {"score": handoff_score, "maximum": 10},
        },
        "diagnostics": {
            "required_evidence": sorted(required_keys),
            "actual_evidence": sorted(actual_keys),
            "missing_evidence": missing_keys,
            "bad_evidence_ids": bad_references,
            "possibility_estimate_errors": possibility_errors,
            "claim_violations": claim_violations,
            "called_tools": sorted(called_tool_set),
            "evidence_calls": len(evidence),
        },
        "runtime": {
            "duration_seconds": round(duration_seconds, 3),
            "usage": usage,
        },
        "human_review": {
            "status": "PENDING",
            "criteria": [
                {"question": question, "decision": "PENDING", "note": ""}
                for question in spec.get("human_review", [])
            ],
        },
    }


def compare_evaluation_runs(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """按 eval_id 与重复序号比较两个评测产物。"""

    def metric(item: dict[str, Any], name: str) -> float | int | None:
        evaluation = item.get("evaluation", {})
        if name == "evidence_calls":
            value = evaluation.get("diagnostics", {}).get(name)
        elif name == "total_tokens":
            usage = evaluation.get("runtime", {}).get("usage") or {}
            value = usage.get(name)
        else:
            value = evaluation.get("runtime", {}).get(name)
        return value if isinstance(value, (int, float)) else None

    def percentage_delta(old: float, new: float) -> float | None:
        if old == 0:
            return None
        return round((new - old) / old, 4)

    def indexed(run: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
        return {(item["eval_id"], int(item["repeat"])): item for item in run.get("runs", [])}

    baseline_runs = indexed(baseline)
    candidate_runs = indexed(candidate)
    common = sorted(baseline_runs.keys() & candidate_runs.keys())
    cases = []
    for key in common:
        old = baseline_runs[key]
        new = candidate_runs[key]
        old_duration = metric(old, "duration_seconds")
        new_duration = metric(new, "duration_seconds")
        old_tokens = metric(old, "total_tokens")
        new_tokens = metric(new, "total_tokens")
        old_calls = metric(old, "evidence_calls")
        new_calls = metric(new, "evidence_calls")
        cases.append(
            {
                "eval_id": key[0],
                "repeat": key[1],
                "baseline_score": old["evaluation"]["score"],
                "candidate_score": new["evaluation"]["score"],
                "score_delta": (new["evaluation"]["score"] - old["evaluation"]["score"]),
                "baseline_pass": old["evaluation"]["automatic_pass"],
                "candidate_pass": new["evaluation"]["automatic_pass"],
                "baseline_duration_seconds": old_duration,
                "candidate_duration_seconds": new_duration,
                "duration_delta_seconds": (
                    round(float(new_duration) - float(old_duration), 3)
                    if old_duration is not None and new_duration is not None
                    else None
                ),
                "baseline_total_tokens": old_tokens,
                "candidate_total_tokens": new_tokens,
                "total_tokens_delta": (
                    int(new_tokens - old_tokens)
                    if old_tokens is not None and new_tokens is not None
                    else None
                ),
                "baseline_evidence_calls": old_calls,
                "candidate_evidence_calls": new_calls,
                "evidence_calls_delta": (
                    int(new_calls - old_calls)
                    if old_calls is not None and new_calls is not None
                    else None
                ),
            }
        )
    old_scores = [float(item["baseline_score"]) for item in cases]
    new_scores = [float(item["candidate_score"]) for item in cases]
    old_pass_rate = sum(bool(item["baseline_pass"]) for item in cases) / len(cases) if cases else 0
    new_pass_rate = sum(bool(item["candidate_pass"]) for item in cases) / len(cases) if cases else 0
    duration_pairs = [
        (float(item["baseline_duration_seconds"]), float(item["candidate_duration_seconds"]))
        for item in cases
        if item["baseline_duration_seconds"] is not None
        and item["candidate_duration_seconds"] is not None
    ]
    token_pairs = [
        (int(item["baseline_total_tokens"]), int(item["candidate_total_tokens"]))
        for item in cases
        if item["baseline_total_tokens"] is not None and item["candidate_total_tokens"] is not None
    ]
    call_pairs = [
        (int(item["baseline_evidence_calls"]), int(item["candidate_evidence_calls"]))
        for item in cases
        if item["baseline_evidence_calls"] is not None
        and item["candidate_evidence_calls"] is not None
    ]
    old_duration_total = sum(pair[0] for pair in duration_pairs)
    new_duration_total = sum(pair[1] for pair in duration_pairs)
    old_token_total = sum(pair[0] for pair in token_pairs)
    new_token_total = sum(pair[1] for pair in token_pairs)
    return {
        "baseline_run_id": baseline.get("run_id"),
        "candidate_run_id": candidate.get("run_id"),
        "matched_runs": len(common),
        "summary": {
            "baseline_mean_score": round(sum(old_scores) / len(old_scores), 2) if cases else 0,
            "candidate_mean_score": round(sum(new_scores) / len(new_scores), 2) if cases else 0,
            "mean_score_delta": (
                round((sum(new_scores) - sum(old_scores)) / len(cases), 2) if cases else 0
            ),
            "baseline_pass_rate": round(old_pass_rate, 4),
            "candidate_pass_rate": round(new_pass_rate, 4),
            "pass_rate_delta": round(new_pass_rate - old_pass_rate, 4),
            "duration_comparable_runs": len(duration_pairs),
            "baseline_duration_seconds": round(old_duration_total, 3),
            "candidate_duration_seconds": round(new_duration_total, 3),
            "duration_delta_seconds": round(new_duration_total - old_duration_total, 3),
            "duration_delta_rate": percentage_delta(old_duration_total, new_duration_total),
            "token_comparable_runs": len(token_pairs),
            "baseline_total_tokens": old_token_total,
            "candidate_total_tokens": new_token_total,
            "total_tokens_delta": new_token_total - old_token_total,
            "total_tokens_delta_rate": percentage_delta(old_token_total, new_token_total),
            "evidence_call_comparable_runs": len(call_pairs),
            "baseline_evidence_calls": sum(pair[0] for pair in call_pairs),
            "candidate_evidence_calls": sum(pair[1] for pair in call_pairs),
            "evidence_calls_delta": sum(pair[1] - pair[0] for pair in call_pairs),
        },
        "cases": cases,
    }


def replace_evaluation_runs(base: dict[str, Any], replacement: dict[str, Any]) -> dict[str, Any]:
    """以同快照、同评测集的定向复跑结果替换基础产物中的对应运行。"""

    for field in ("scope", "dataset_version", "dataset_sha256", "model"):
        if base.get(field) != replacement.get(field):
            raise ValueError(f"定向复跑的 {field} 与基础产物不一致。")
    for field in ("snapshot_id", "schema_fingerprint", "database_sha256"):
        if base.get("data_snapshot", {}).get(field) != replacement.get("data_snapshot", {}).get(
            field
        ):
            raise ValueError(f"定向复跑的数据快照字段 {field} 与基础产物不一致。")

    def key(item: dict[str, Any]) -> tuple[str, int]:
        return item["eval_id"], int(item["repeat"])

    base_runs = {key(item): item for item in base.get("runs", [])}
    replacement_runs = {key(item): item for item in replacement.get("runs", [])}
    if not replacement_runs:
        raise ValueError("定向复跑产物没有可替换的运行。")
    unknown = replacement_runs.keys() - base_runs.keys()
    if unknown:
        raise ValueError(f"定向复跑包含基础产物不存在的运行：{sorted(unknown)}。")

    result = deepcopy(base)
    result["source_run_ids"] = [base.get("run_id"), replacement.get("run_id")]
    result["runs"] = [replacement_runs.get(key(item), item) for item in base.get("runs", [])]
    return result


def apply_human_reviews(
    evaluation_run: dict[str, Any], review_file: dict[str, Any]
) -> dict[str, Any]:
    """把逐项人工语义复核合入评测产物，并计算最终发布门槛。"""

    if review_file.get("run_id") != evaluation_run.get("run_id"):
        raise ValueError("人工复核文件的 run_id 与评测产物不一致。")
    review_index = {
        (item["eval_id"], int(item["repeat"])): item for item in review_file.get("reviews", [])
    }
    result = deepcopy(evaluation_run)
    human_reviewed = 0
    human_passed = 0
    release_passed = 0
    for run in result.get("runs", []):
        key = (run["eval_id"], int(run["repeat"]))
        review = review_index.get(key)
        evaluation = run["evaluation"]
        evaluation["release_pass"] = False
        if review is None:
            continue
        expected = evaluation["human_review"]["criteria"]
        decisions = review.get("criteria", [])
        if len(decisions) != len(expected):
            raise ValueError(f"{key} 的人工复核项数量与评测集不一致。")
        merged = []
        for expected_item, decision in zip(expected, decisions, strict=True):
            if decision.get("question") != expected_item["question"]:
                raise ValueError(f"{key} 的人工复核问题与评测集不一致。")
            value = decision.get("decision")
            if value not in {"PASS", "FAIL"}:
                raise ValueError(f"{key} 的人工复核 decision 必须是 PASS 或 FAIL。")
            merged.append(
                {
                    "question": expected_item["question"],
                    "decision": value,
                    "note": str(decision.get("note", "")),
                }
            )
        human_pass = all(item["decision"] == "PASS" for item in merged)
        evaluation["human_review"] = {
            "status": "COMPLETED",
            "reviewer": review_file.get("reviewer"),
            "reviewed_at": review_file.get("reviewed_at"),
            "passed": human_pass,
            "criteria": merged,
        }
        evaluation["release_pass"] = bool(evaluation["automatic_pass"] and human_pass)
        human_reviewed += 1
        human_passed += int(human_pass)
        release_passed += int(evaluation["release_pass"])
    summary = result.setdefault("summary", {})
    total = len(result.get("runs", []))
    summary.update(
        {
            "human_reviewed": human_reviewed,
            "human_passed": human_passed,
            "release_passed": release_passed,
            "release_pass_rate": round(release_passed / total, 4) if total else 0,
        }
    )
    result["review_file"] = {
        "reviewer": review_file.get("reviewer"),
        "reviewed_at": review_file.get("reviewed_at"),
    }
    return result

"""运行调查评测集；默认调用真实 DeepSeek，但不会写入案件库。"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ict_agent.agent import allowed_investigation_tools, run_investigation_agent
from ict_agent.config import load_settings
from ict_agent.models import InvestigationReport
from ict_agent.service import get_case_detail

EVAL_PATH = Path(__file__).with_name("investigation_cases.json")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ABSTENTION_MARKERS = ("无法判断", "不能判断", "没有证据", "证据不足", "尚无数据", "不能证明")


def _report_sentences(report: InvestigationReport) -> list[str]:
    text = "。".join(
        [
            report.investigation_summary,
            *(fact.statement for fact in report.facts),
            *(hypothesis.statement for hypothesis in report.hypotheses),
        ]
    )
    return [item.strip() for item in re.split(r"[。！？;；\n]", text) if item.strip()]


def _automatic_checks(
    spec: dict[str, Any], report: InvestigationReport, evidence: list[Any]
) -> dict[str, Any]:
    valid_ids = {item.evidence_id for item in evidence}
    called_tools = {item.tool_name for item in evidence}
    required_tools = set(spec.get("required_tools", []))
    required_evidence = {
        (item["dataset"], item["grain"]) for item in spec.get("required_evidence", [])
    }
    actual_evidence = {
        (item.arguments.get("dataset"), item.arguments.get("grain"))
        for item in evidence
        if item.tool_name == "query_business_evidence"
    }
    bad_refs = sorted(
        {
            evidence_id
            for fact in report.facts
            for evidence_id in fact.evidence_ids
            if evidence_id not in valid_ids
        }
        | {
            evidence_id
            for evidence_id in (
                report.risk_assessment.evidence_ids if report.risk_assessment else []
            )
            if evidence_id not in valid_ids
        }
        | {
            evidence_id
            for hypothesis in report.hypotheses
            for evidence_id in (
                hypothesis.supporting_evidence_ids + hypothesis.contradicting_evidence_ids
            )
            if evidence_id not in valid_ids
        }
    )
    violations = []
    forbidden_claims = spec["global_forbidden_unqualified_claims"]
    for sentence in _report_sentences(report):
        for claim in forbidden_claims:
            if claim in sentence and not any(marker in sentence for marker in ABSTENTION_MARKERS):
                violations.append({"claim": claim, "sentence": sentence})
    status_errors = []
    for hypothesis in report.hypotheses:
        if hypothesis.status == "SUPPORTED" and not hypothesis.supporting_evidence_ids:
            status_errors.append(f"{hypothesis.hypothesis_id}: SUPPORTED 无支持证据")
        if hypothesis.status == "WEAKENED" and not hypothesis.contradicting_evidence_ids:
            status_errors.append(f"{hypothesis.hypothesis_id}: WEAKENED 无反驳证据")
        if (
            hypothesis.status == "UNRESOLVED"
            and not hypothesis.missing_evidence
            and not (hypothesis.supporting_evidence_ids and hypothesis.contradicting_evidence_ids)
        ):
            status_errors.append(f"{hypothesis.hypothesis_id}: UNRESOLVED 未说明缺失或冲突")
    checks = {
        "tool_coverage": required_tools <= called_tools,
        "evidence_query_coverage": required_evidence <= actual_evidence,
        "risk_signal_present": report.risk_assessment is not None,
        "risk_signal_is_actionable": (
            report.risk_assessment is not None and report.risk_assessment.stage != "LIMITED"
        ),
        "citation_integrity": not bad_refs,
        "hypothesis_status_integrity": not status_errors,
        "no_unqualified_forbidden_claims": not violations,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "missing_tools": sorted(required_tools - called_tools),
        "missing_evidence_queries": sorted(required_evidence - actual_evidence),
        "bad_evidence_ids": bad_refs,
        "status_errors": status_errors,
        "claim_violations": violations,
    }


async def _run(selected_case_ids: set[str]) -> dict[str, Any]:
    dataset = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    settings = load_settings(require_api_key=True, require_data_dir=False)
    results = []
    for case_spec in dataset["cases"]:
        if selected_case_ids and case_spec["case_id"] not in selected_case_ids:
            continue
        case = get_case_detail(case_spec["case_id"], settings=settings)
        if case.case_type == "INVENTORY":
            expected_tools = set(allowed_investigation_tools(case.case_type))
            if expected_tools != set(case_spec.get("required_tools", [])):
                raise ValueError(f"{case_spec['eval_id']} 的工具清单与当前 Agent 契约不一致。")
        try:
            outcome = await run_investigation_agent(settings, case)
            checks = _automatic_checks(
                {
                    **case_spec,
                    "global_forbidden_unqualified_claims": dataset[
                        "global_forbidden_unqualified_claims"
                    ],
                },
                outcome.report,
                outcome.evidence,
            )
            results.append(
                {
                    "eval_id": case_spec["eval_id"],
                    "case_id": case_spec["case_id"],
                    "passed": checks["passed"],
                    "automatic_checks": checks,
                    "human_review": case_spec["human_review"],
                    "report": outcome.report.model_dump(mode="json"),
                    "evidence": [item.model_dump(mode="json") for item in outcome.evidence],
                }
            )
        except Exception as exc:
            results.append(
                {
                    "eval_id": case_spec["eval_id"],
                    "case_id": case_spec["case_id"],
                    "passed": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "human_review": case_spec["human_review"],
                }
            )
    return {
        "dataset_version": dataset["version"],
        "model": settings.deepseek_model,
        "created_at": datetime.now(UTC).isoformat(),
        "summary": {
            "total": len(results),
            "passed": sum(1 for item in results if item["passed"]),
        },
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="运行真实 DeepSeek 案件调查评测。")
    parser.add_argument(
        "--case-id", action="append", default=[], help="只运行指定案件，可重复传入。"
    )
    args = parser.parse_args()
    result = asyncio.run(_run(set(args.case_id)))
    output_dir = PROJECT_ROOT / "artifacts"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_path = output_dir / f"investigation-eval-{timestamp}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output_path), **result["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

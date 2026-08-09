"""运行或比较调查 Agent 评测；案件输入已冻结，不调用规则引擎或案件库。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ict_agent.agent import run_investigation_agent
from ict_agent.config import load_settings
from ict_agent.data import DuckDBStore
from ict_agent.evaluation import (
    apply_human_reviews,
    compare_evaluation_runs,
    evaluate_investigation_run,
    replace_evaluation_runs,
)
from ict_agent.models import Evidence, InvestigationCaseInput, InvestigationReport

EVAL_PATH = Path(__file__).with_name("investigation_cases.json")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision() -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [float(item["evaluation"]["score"]) for item in runs]
    passed = sum(bool(item["evaluation"]["automatic_pass"]) for item in runs)
    total_tokens = sum(
        int((item["evaluation"]["runtime"].get("usage") or {}).get("total_tokens") or 0)
        for item in runs
    )
    return {
        "total_runs": len(runs),
        "completed_runs": sum(item.get("report") is not None for item in runs),
        "partial_runs": sum(bool(item.get("partial")) for item in runs),
        "error_runs": sum(bool(item.get("error")) for item in runs),
        "automatic_passed": passed,
        "automatic_pass_rate": round(passed / len(runs), 4) if runs else 0,
        "mean_score": round(sum(scores) / len(scores), 2) if scores else 0,
        "minimum_score": min(scores) if scores else 0,
        "maximum_score": max(scores) if scores else 0,
        "total_duration_seconds": round(
            sum(float(item["evaluation"]["runtime"]["duration_seconds"]) for item in runs),
            3,
        ),
        "total_tokens": total_tokens,
    }


def _stability(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in runs:
        grouped[item["eval_id"]].append(item)
    result = []
    for eval_id, items in sorted(grouped.items()):
        scores = [float(item["evaluation"]["score"]) for item in items]
        stages = {
            item["report"]["risk_assessment"]["stage"] for item in items if item.get("report")
        }
        passes = {bool(item["evaluation"]["automatic_pass"]) for item in items}
        result.append(
            {
                "eval_id": eval_id,
                "runs": len(items),
                "mean_score": round(sum(scores) / len(scores), 2),
                "score_range": round(max(scores) - min(scores), 2),
                "stage_consistent": len(stages) <= 1,
                "automatic_pass_consistent": len(passes) <= 1,
            }
        )
    return result


async def _run(
    dataset_path: Path,
    selected_eval_ids: set[str],
    repeats: int,
    label: str,
) -> dict[str, Any]:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    settings = load_settings(require_api_key=True, require_data_dir=False)
    selected_specs = [
        item
        for item in dataset["cases"]
        if not selected_eval_ids or item["eval_id"] in selected_eval_ids
    ]
    unknown = selected_eval_ids - {item["eval_id"] for item in selected_specs}
    if unknown:
        raise ValueError(f"评测集中不存在：{sorted(unknown)}")

    runs: list[dict[str, Any]] = []
    for spec in selected_specs:
        case_input = InvestigationCaseInput.model_validate(spec["input"])
        for repeat in range(1, repeats + 1):
            started = time.perf_counter()
            report = None
            evidence = []
            called_tools: tuple[str, ...] = ()
            partial = False
            usage = None
            error = None
            try:
                outcome = await run_investigation_agent(settings, case_input)
                report = outcome.report
                evidence = outcome.evidence
                called_tools = outcome.called_tools
                partial = outcome.partial
                usage = outcome.usage
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            duration = time.perf_counter() - started
            evaluation = evaluate_investigation_run(
                spec,
                report=report,
                evidence=evidence,
                called_tools=called_tools,
                partial=partial,
                error=error,
                duration_seconds=duration,
                usage=usage,
                forbidden_claims=dataset["global_forbidden_unqualified_claims"],
            )
            runs.append(
                {
                    "eval_id": spec["eval_id"],
                    "case_id": case_input.case_id,
                    "repeat": repeat,
                    "purpose": spec["purpose"],
                    "partial": partial,
                    "error": error,
                    "evaluation": evaluation,
                    "report": report.model_dump(mode="json") if report else None,
                    "evidence": [item.model_dump(mode="json") for item in evidence],
                }
            )

    created_at = datetime.now(UTC).isoformat()
    database_path = settings.database_path
    snapshot = DuckDBStore(database_path).get_snapshot()
    return {
        "run_id": f"{label}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        "label": label,
        "created_at": created_at,
        "scope": "INVESTIGATION_AGENT_ONLY",
        "dataset_version": dataset["version"],
        "dataset_sha256": _sha256(dataset_path),
        "model": settings.deepseek_model,
        "git_revision": _git_revision(),
        "data_snapshot": {
            "snapshot_id": snapshot.snapshot_id,
            "schema_fingerprint": snapshot.schema_fingerprint,
            "source_sha256": {item.table: item.sha256 for item in snapshot.sources},
            "database_size_bytes": database_path.stat().st_size,
            "database_sha256": _sha256(database_path),
        },
        "repeats": repeats,
        "summary": _summary(runs),
        "stability": _stability(runs),
        "runs": runs,
    }


def _markdown_summary(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        f"# 调查 Agent 评测：{result['label']}",
        "",
        f"- Run ID：`{result['run_id']}`",
        f"- 模型：`{result['model']}`",
        f"- 数据集版本：`{result['dataset_version']}`",
        f"- 自动通过：{summary['automatic_passed']}/{summary['total_runs']} "
        f"（{summary['automatic_pass_rate']:.1%}）",
        f"- 平均分：{summary['mean_score']}/100",
        f"- 总耗时：{summary['total_duration_seconds']} 秒",
        f"- 总 Token：{summary['total_tokens']}",
        "",
        "| 评测项 | 重复 | 分数 | 自动门槛 | 完整/部分 |",
        "|---|---:|---:|---|---|",
    ]
    for item in result["runs"]:
        evaluation = item["evaluation"]
        lines.append(
            f"| {item['eval_id']} | {item['repeat']} | {evaluation['score']} | "
            f"{'通过' if evaluation['automatic_pass'] else '未通过'} | "
            f"{'部分' if item['partial'] else '完整' if item['report'] else '失败'} |"
        )
    lines.extend(
        [
            "",
            "> 自动分只覆盖可确定验证的结构与证据底线；每次运行的语义问题仍需完成"
            " `human_review` 清单后才能作为人工验收结论。",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_result(result: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    output_path.with_suffix(".md").write_text(_markdown_summary(result), encoding="utf-8")


def _rescore(result: dict[str, Any], dataset_path: Path) -> dict[str, Any]:
    """用当前评测规则重新评分原始报告和证据，不调用模型。"""

    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    specs = {item["eval_id"]: item for item in dataset["cases"]}
    rescored_runs = []
    for item in result.get("runs", []):
        spec = specs.get(item["eval_id"])
        if spec is None:
            raise ValueError(f"当前评测集不存在 {item['eval_id']}。")
        report_payload = item.get("report")
        report = (
            InvestigationReport.model_validate({**report_payload, "trace": []})
            if report_payload
            else None
        )
        evidence = [Evidence.model_construct(**value) for value in item.get("evidence", [])]
        old_evaluation = item["evaluation"]
        runtime = old_evaluation["runtime"]
        evaluation = evaluate_investigation_run(
            spec,
            report=report,
            evidence=evidence,
            called_tools=old_evaluation["diagnostics"].get("called_tools", []),
            partial=bool(item.get("partial")),
            error=item.get("error"),
            duration_seconds=float(runtime["duration_seconds"]),
            usage=runtime.get("usage"),
            forbidden_claims=dataset["global_forbidden_unqualified_claims"],
        )
        rescored_runs.append({**item, "evaluation": evaluation})
    return {
        **result,
        "dataset_version": dataset["version"],
        "dataset_sha256": _sha256(dataset_path),
        "rescored_at": datetime.now(UTC).isoformat(),
        "summary": _summary(rescored_runs),
        "stability": _stability(rescored_runs),
        "runs": rescored_runs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="运行或比较真实 DeepSeek 调查 Agent 评测。")
    parser.add_argument("--dataset", type=Path, default=EVAL_PATH, help="冻结评测集路径。")
    parser.add_argument("--eval-id", action="append", default=[], help="只运行指定评测项。")
    parser.add_argument("--repeats", type=int, default=1, help="每个评测项重复次数。")
    parser.add_argument("--label", default="candidate", help="本次运行标签。")
    parser.add_argument("--output", type=Path, help="结果 JSON 路径。")
    parser.add_argument(
        "--compare",
        nargs=2,
        type=Path,
        metavar=("BASELINE", "CANDIDATE"),
        help="比较两个已有 JSON 产物，不调用模型。",
    )
    parser.add_argument("--rescore", type=Path, help="按当前评测规则重新评分已有产物。")
    parser.add_argument("--review-template", type=Path, help="从已有评测产物生成逐项人工复核模板。")
    parser.add_argument(
        "--apply-reviews",
        nargs=2,
        type=Path,
        metavar=("RUN", "REVIEWS"),
        help="合并人工语义复核并计算最终发布门槛。",
    )
    parser.add_argument(
        "--replace-runs",
        nargs=2,
        type=Path,
        metavar=("BASE", "REPLACEMENT"),
        help="把同快照、同评测集的定向复跑替换进完整基础产物。",
    )
    args = parser.parse_args()
    if args.repeats < 1 or args.repeats > 5:
        parser.error("--repeats 必须在 1 到 5 之间。")

    output_dir = PROJECT_ROOT / "artifacts" / "investigation-evals"
    modes = (
        args.compare,
        args.rescore,
        args.review_template,
        args.apply_reviews,
        args.replace_runs,
    )
    if sum(bool(item) for item in modes) > 1:
        parser.error(
            "--compare、--rescore、--review-template、--apply-reviews 和 --replace-runs "
            "只能选择一项。"
        )
    if args.compare:
        baseline = json.loads(args.compare[0].read_text(encoding="utf-8"))
        candidate = json.loads(args.compare[1].read_text(encoding="utf-8"))
        result = compare_evaluation_runs(baseline, candidate)
        output = args.output or output_dir / (
            f"comparison-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"output": str(output), **result["summary"]}, ensure_ascii=False))
        return

    if args.rescore:
        original = json.loads(args.rescore.read_text(encoding="utf-8"))
        result = _rescore(original, args.dataset)
        output = args.output or output_dir / (
            f"{result['label']}-rescored-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
        )
        _write_result(result, output)
        print(json.dumps({"output": str(output), **result["summary"]}, ensure_ascii=False))
        return

    if args.review_template:
        original = json.loads(args.review_template.read_text(encoding="utf-8"))
        template = {
            "run_id": original["run_id"],
            "reviewer": "",
            "reviewed_at": "",
            "reviews": [
                {
                    "eval_id": item["eval_id"],
                    "repeat": item["repeat"],
                    "criteria": [
                        {
                            "question": criterion["question"],
                            "decision": "PENDING",
                            "note": "",
                        }
                        for criterion in item["evaluation"]["human_review"]["criteria"]
                    ],
                }
                for item in original["runs"]
            ],
        }
        output = args.output or output_dir / (
            f"{original['label']}-review-template-"
            f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"output": str(output)}, ensure_ascii=False))
        return

    if args.apply_reviews:
        original = json.loads(args.apply_reviews[0].read_text(encoding="utf-8"))
        reviews = json.loads(args.apply_reviews[1].read_text(encoding="utf-8"))
        result = apply_human_reviews(original, reviews)
        output = args.output or output_dir / (
            f"{result['label']}-reviewed-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
        )
        _write_result(result, output)
        print(json.dumps({"output": str(output), **result["summary"]}, ensure_ascii=False))
        return

    if args.replace_runs:
        base = json.loads(args.replace_runs[0].read_text(encoding="utf-8"))
        replacement = json.loads(args.replace_runs[1].read_text(encoding="utf-8"))
        result = replace_evaluation_runs(base, replacement)
        result.update(
            {
                "run_id": f"{args.label}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
                "label": args.label,
                "created_at": datetime.now(UTC).isoformat(),
                "git_revision": _git_revision(),
                "summary": _summary(result["runs"]),
                "stability": _stability(result["runs"]),
            }
        )
        output = args.output or output_dir / f"{result['run_id']}.json"
        _write_result(result, output)
        print(json.dumps({"output": str(output), **result["summary"]}, ensure_ascii=False))
        return

    result = asyncio.run(_run(args.dataset, set(args.eval_id), args.repeats, args.label))
    output = args.output or output_dir / f"{result['run_id']}.json"
    _write_result(result, output)
    print(json.dumps({"output": str(output), **result["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

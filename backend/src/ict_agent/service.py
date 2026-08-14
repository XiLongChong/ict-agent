"""经营分析、风险案件、调查事件流与人工审核的应用服务。"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from pydantic_ai.models import Model

from ict_agent.agent import (
    InvestigationAgentProgress,
    InvestigationOutcome,
    build_investigation_case_input,
    stream_investigation_agent,
)
from ict_agent.config import ConfigurationError, Settings, load_settings
from ict_agent.data import (
    CaseStore,
    DataAccessError,
    DatabaseScalar,
    DuckDBStore,
    HealthScoreWrite,
    InvestigationWrite,
    ReviewWrite,
)
from ict_agent.health import compute_health_scores
from ict_agent.listmgmt import (
    build_recommendations,
    current_list_from_credit,
    review_recommendation,
)
from ict_agent.models import (
    AlertResponse,
    CaseStatus,
    CaseType,
    DashboardResponse,
    DataSnapshotResponse,
    DataSourceSnapshot,
    HealthScoreResponse,
    InvestigationCaseInput,
    InvestigationRecord,
    InvestigationStreamEvent,
    ListRecommendationResponse,
    ListRecommendationReviewRequest,
    PreAssessmentResponse,
    ProjectViewResponse,
    ReviewDecision,
    ReviewRecord,
    ReviewRequest,
    RiskCaseDetail,
    RiskCaseSummary,
    RiskOverviewResponse,
    RiskPriority,
    RuleHit,
    RuleRunResponse,
    SentimentResponse,
    SentimentVerifyRequest,
    WarningOverviewResponse,
)
from ict_agent.project import list_new_projects, list_projects, run_pre_assessment
from ict_agent.rules import build_rule_scan
from ict_agent.sentiment import list_sentiments, verify_sentiment
from ict_agent.simdata import SimulatedData, load_simulated_data
from ict_agent.tools import (
    get_ar_trend,
    get_business_overview,
    get_inventory_health,
    get_latest_ar_summary,
)

logger = logging.getLogger(__name__)


class ServiceError(RuntimeError):
    """可安全映射到 HTTP 的应用错误。"""

    def __init__(self, message: str, request_id: str, status_code: int) -> None:
        super().__init__(message)
        self.request_id = request_id
        self.status_code = status_code


def get_dashboard(*, settings: Settings | None = None) -> DashboardResponse:
    """获取无需模型参与的首页确定性指标。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False)
        store = DuckDBStore(runtime_settings.database_path)
        store.ensure_ready()
        return DashboardResponse(
            overview=get_business_overview(store),
            latest_ar=get_latest_ar_summary(store),
            inventory=get_inventory_health(store),
            ar_trend=get_ar_trend(store),
        )
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc
    except Exception as exc:
        raise ServiceError("首页分析失败，请重新导入数据后重试。", request_id, 500) from exc


def get_data_snapshot(*, settings: Settings | None = None) -> DataSnapshotResponse:
    """返回当前业务库的来源哈希和模式身份，不暴露本机路径。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        snapshot = DuckDBStore(runtime_settings.database_path).get_snapshot()
        return DataSnapshotResponse(
            snapshot_id=snapshot.snapshot_id,
            imported_at=snapshot.imported_at,
            schema_fingerprint=snapshot.schema_fingerprint,
            sources=[DataSourceSnapshot(**item.__dict__) for item in snapshot.sources],
        )
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


def _as_int(value: DatabaseScalar) -> int:
    if value is None:
        return 0
    return int(value)


def _as_float(value: DatabaseScalar) -> float:
    if value is None:
        return 0.0
    return float(value)


def _case_status(value: DatabaseScalar) -> CaseStatus:
    raw = str(value)
    if raw == "AGENT_REVIEWING":
        return "PENDING_AGENT_REVIEW"
    return cast(CaseStatus, raw)


def _risk_priority(value: DatabaseScalar) -> RiskPriority:
    raw = str(value)
    if raw == "CRITICAL":
        return "HIGH"
    return cast(RiskPriority, raw)


def _case_summary(row: tuple[DatabaseScalar, ...]) -> RiskCaseSummary:
    return RiskCaseSummary(
        case_id=str(row[0]),
        case_type=cast(CaseType, str(row[1])),
        entity_type=str(row[2]),
        entity_id=str(row[3]),
        entity_label=str(row[4]),
        observation_date=str(row[5]).split("T", maxsplit=1)[0],
        status=_case_status(row[6]),
        priority=_risk_priority(row[7]),
        exposure_amount=_as_float(row[8]),
        summary=str(row[9]),
        risk_overview=str(row[13]),
        rule_hit_count=_as_int(row[10]),
        rule_set_version=str(row[11]),
        updated_at=str(row[12]),
    )


def _rule_run(row: tuple[DatabaseScalar, ...]) -> RuleRunResponse:
    return RuleRunResponse(
        run_id=str(row[0]),
        rule_set_version=str(row[1]),
        observation_date=str(row[2]).split("T", maxsplit=1)[0],
        cases_detected=_as_int(row[3]),
        cases_created=_as_int(row[4]),
        rule_hits=_as_int(row[5]),
        receivable_cases=_as_int(row[6]),
        inventory_cases=_as_int(row[7]),
        created_at=str(row[8]),
    )


def run_rule_scan(*, settings: Settings | None = None) -> RuleRunResponse:
    """执行一次确定性规则扫描并幂等写入案件库。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        business_store = DuckDBStore(runtime_settings.database_path)
        business_store.ensure_ready()
        draft = build_rule_scan(business_store)
        case_store = CaseStore(runtime_settings.case_database_path)
        created = case_store.save_rule_scan(draft.run, draft.cases, draft.hits)
        return RuleRunResponse(
            run_id=draft.run.run_id,
            rule_set_version=draft.run.rule_set_version,
            observation_date=draft.run.observation_date,
            cases_detected=draft.run.cases_detected,
            cases_created=created,
            rule_hits=draft.run.rule_hits,
            receivable_cases=draft.run.receivable_cases,
            inventory_cases=draft.run.inventory_cases,
            created_at=draft.run.created_at,
        )
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc
    except Exception as exc:
        raise ServiceError("风险规则扫描失败，请检查数据后重试。", request_id, 500) from exc


def list_cases(
    *,
    status: CaseStatus | None = None,
    case_type: CaseType | None = None,
    limit: int = 200,
    settings: Settings | None = None,
) -> list[RiskCaseSummary]:
    """查询风险案件队列。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        result = CaseStore(runtime_settings.case_database_path).fetch_cases(
            status=status, case_type=case_type, limit=limit
        )
        return [_case_summary(tuple(row)) for row in result.rows]
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


def get_risk_overview(*, settings: Settings | None = None) -> RiskOverviewResponse:
    """获取风险案件总览。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        store = CaseStore(runtime_settings.case_database_path)
        overview_row = store.fetch_overview().rows[0]
        latest_rows = store.fetch_latest_run().rows
        return RiskOverviewResponse(
            latest_run=_rule_run(tuple(latest_rows[0])) if latest_rows else None,
            total_cases=_as_int(overview_row[0]),
            pending_agent_cases=_as_int(overview_row[1]),
            pending_human_review_cases=_as_int(overview_row[2]),
            action_in_progress_cases=_as_int(overview_row[3]),
            closed_cases=_as_int(overview_row[4]),
            high_priority_cases=_as_int(overview_row[5]),
            exposure_amount=_as_float(overview_row[6]),
            cases_by_type={
                "ACCOUNTS_RECEIVABLE": _as_int(overview_row[7]),
                "INVENTORY": _as_int(overview_row[8]),
            },
        )
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


def get_case_detail(
    case_id: str,
    *,
    settings: Settings | None = None,
) -> RiskCaseDetail:
    """获取规则、最新调查和审核历史组成的案件详情。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        store = CaseStore(runtime_settings.case_database_path)
        case_rows = store.fetch_case(case_id).rows
        if not case_rows:
            raise ServiceError("未找到指定风险案件。", request_id, 404)
        row = case_rows[0]
        summary = _case_summary(
            (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[6],
                row[7],
                row[8],
                row[9],
                row[10],
                row[11],
                row[12],
                row[13],
                row[14],
            )
        )
        hits = [
            RuleHit(
                rule_hit_id=str(hit[0]),
                rule_id=str(hit[1]),
                rule_name=str(hit[2]),
                rule_version=str(hit[3]),
                severity=_risk_priority(hit[4]),
                exposure_amount=_as_float(hit[5]),
                reason=str(hit[6]),
                metrics=json.loads(str(hit[7])),
                threshold_source=str(hit[8]),
                sources=json.loads(str(hit[9])),
                period=str(hit[10]),
            )
            for hit in store.fetch_rule_hits(case_id).rows
        ]
        investigation_rows = store.fetch_latest_investigation(case_id).rows
        latest_investigation = None
        if investigation_rows:
            investigation = investigation_rows[0]
            latest_investigation = InvestigationRecord(
                investigation_id=str(investigation[0]),
                case_id=str(investigation[1]),
                report=json.loads(str(investigation[2])),
                evidence=json.loads(str(investigation[3])),
                created_at=str(investigation[4]),
            )
        reviews = [
            ReviewRecord(
                review_id=str(review[0]),
                case_id=str(review[1]),
                decision=cast(ReviewDecision, str(review[2])),
                reviewer=str(review[3]),
                reason=str(review[4]),
                created_at=str(review[5]),
            )
            for review in store.fetch_reviews(case_id).rows
        ]
        return RiskCaseDetail(
            **summary.model_dump(),
            entity_context=json.loads(str(row[5])),
            rule_hits=hits,
            latest_investigation=latest_investigation,
            reviews=reviews,
        )
    except ServiceError:
        raise
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


@dataclass(frozen=True)
class PreparedInvestigation:
    """已经完成同步校验、可安全开始流式响应的调查上下文。"""

    settings: Settings
    case: RiskCaseDetail
    investigation_input: InvestigationCaseInput
    model: Model | None


def prepare_investigation(
    case_id: str,
    *,
    settings: Settings | None = None,
    model: Model | None = None,
) -> PreparedInvestigation:
    """在 HTTP 流开始前检查配置、案件和业务数据库。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(
            require_api_key=model is None, require_data_dir=False
        )
        case = get_case_detail(case_id, settings=runtime_settings)
        if case.status != "PENDING_AGENT_REVIEW":
            raise ServiceError("当前案件状态不允许启动 Agent 调查。", request_id, 409)
        DuckDBStore(runtime_settings.database_path).ensure_ready()
        store = CaseStore(runtime_settings.case_database_path)
        if not store.transition_case(case_id, "PENDING_AGENT_REVIEW", "AGENT_REVIEWING"):
            raise ServiceError("该案件正在调查，请等待本轮结束后重试。", request_id, 409)
        return PreparedInvestigation(
            settings=runtime_settings,
            case=case,
            investigation_input=build_investigation_case_input(case),
            model=model,
        )
    except ServiceError:
        raise
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


def recover_interrupted_investigations(*, settings: Settings | None = None) -> int:
    """恢复服务异常退出时遗留的临时调查状态。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        return CaseStore(runtime_settings.case_database_path).recover_interrupted_investigations()
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


def _save_investigation(
    prepared: PreparedInvestigation, outcome: InvestigationOutcome
) -> InvestigationRecord:
    created_at = datetime.now(UTC).isoformat()
    record = InvestigationRecord(
        investigation_id=uuid4().hex,
        case_id=prepared.case.case_id,
        report=outcome.report,
        evidence=outcome.evidence,
        created_at=created_at,
    )
    CaseStore(prepared.settings.case_database_path).save_investigation(
        InvestigationWrite(
            investigation_id=record.investigation_id,
            case_id=record.case_id,
            report_json=record.report.model_dump_json(),
            evidence_json=json.dumps(
                [item.model_dump(mode="json") for item in record.evidence],
                ensure_ascii=False,
            ),
            created_at=record.created_at,
        )
    )
    return record


async def stream_prepared_investigation(
    prepared: PreparedInvestigation,
) -> AsyncIterator[InvestigationStreamEvent]:
    """流式执行调查，结束时保存完整或部分报告。"""

    sequence = 1
    report_saved = False
    try:
        yield InvestigationStreamEvent(
            sequence=sequence,
            event_type="RUN_STARTED",
            message="本轮调查已启动，正在发现数据并核对证据。",
        )
        async for event in stream_investigation_agent(
            prepared.settings, prepared.investigation_input, model=prepared.model
        ):
            sequence += 1
            if isinstance(event, InvestigationAgentProgress):
                yield InvestigationStreamEvent(
                    sequence=sequence,
                    event_type=event.event_type,
                    message=event.message,
                    tool_name=event.tool_name,
                    evidence=event.evidence,
                )
                continue
            record = _save_investigation(prepared, event)
            report_saved = True
            yield InvestigationStreamEvent(
                sequence=sequence,
                event_type="REPORT_COMPLETED",
                message=(
                    "调查未完整完成，部分证据与无法判断报告已保存。"
                    if event.partial
                    else "调查报告通过证据校验并已保存，等待人工审核。"
                ),
                record=record,
            )
    except Exception:
        logger.exception("调查事件流执行失败：case_id=%s", prepared.case.case_id)
        sequence += 1
        yield InvestigationStreamEvent(
            sequence=sequence,
            event_type="ERROR",
            message="DeepSeek 调查未能开始，请检查 API Key、账户余额和网络后重试。",
        )
    finally:
        if not report_saved:
            CaseStore(prepared.settings.case_database_path).transition_case(
                prepared.case.case_id, "AGENT_REVIEWING", "PENDING_AGENT_REVIEW"
            )


async def investigate_case(
    case_id: str,
    *,
    settings: Settings | None = None,
    model: Model | None = None,
) -> InvestigationRecord:
    """非流式调用入口，供自动化测试和离线评测复用。"""

    request_id = uuid4().hex
    error_message: str | None = None
    prepared = prepare_investigation(case_id, settings=settings, model=model)
    async for event in stream_prepared_investigation(prepared):
        if event.event_type == "REPORT_COMPLETED" and event.record is not None:
            return event.record
        if event.event_type == "ERROR":
            error_message = event.message
    message = error_message or "DeepSeek 案件调查未产生报告。"
    exc = RuntimeError(message)
    raise ServiceError(message, request_id, 502) from exc


def review_case(
    case_id: str,
    request: ReviewRequest,
    *,
    settings: Settings | None = None,
) -> ReviewRecord:
    """保存人工审核并推进案件状态。"""

    request_id = uuid4().hex
    status_by_decision: dict[ReviewDecision, CaseStatus] = {
        "CONFIRMED_RISK": "ACTION_IN_PROGRESS",
        "NEEDS_MORE_EVIDENCE": "PENDING_AGENT_REVIEW",
        "NO_RISK": "CLOSED",
    }
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        store = CaseStore(runtime_settings.case_database_path)
        case_rows = store.fetch_case(case_id).rows
        if not case_rows:
            raise ServiceError("未找到指定风险案件。", request_id, 404)
        if str(case_rows[0][7]) != "PENDING_HUMAN_REVIEW":
            raise ServiceError("当前案件状态不允许提交人工复核。", request_id, 409)
        created_at = datetime.now(UTC).isoformat()
        record = ReviewWrite(
            review_id=uuid4().hex,
            case_id=case_id,
            decision=request.decision,
            reviewer=request.reviewer,
            reason=request.reason,
            created_at=created_at,
        )
        store.save_review(record, status_by_decision[request.decision])
        return ReviewRecord(
            review_id=record.review_id,
            case_id=record.case_id,
            decision=request.decision,
            reviewer=record.reviewer,
            reason=record.reason,
            created_at=record.created_at,
        )
    except ServiceError:
        raise
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


# ---------------------------------------------------------------------------
# 阶段 A：风险预警系统服务
# ---------------------------------------------------------------------------


def _simulated_data(settings: Settings) -> SimulatedData:
    return load_simulated_data(settings.simulated_data_dir)


def _health_score_response(row: tuple[DatabaseScalar, ...]) -> HealthScoreResponse:
    return HealthScoreResponse(
        id=str(row[0]),
        subject_type=str(row[1]),
        subject_id=str(row[2]),
        subject_label=str(row[3]),
        score=_as_float(row[4]),
        grade=str(row[5]),
        dimensions=json.loads(str(row[6])),
        drivers=json.loads(str(row[7])),
        trend=json.loads(str(row[8])),
        computed_at=str(row[9]),
        data_snapshot_id=str(row[10]),
    )


def list_health_scores(
    *,
    subject_type: str | None = None,
    grade: str | None = None,
    settings: Settings | None = None,
) -> list[HealthScoreResponse]:
    """返回健康度列表。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        result = CaseStore(runtime_settings.case_database_path).fetch_health_scores(
            subject_type=subject_type, grade=grade
        )
        return [_health_score_response(tuple(row)) for row in result.rows]
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


def get_health_score(
    score_id: str,
    *,
    settings: Settings | None = None,
) -> HealthScoreResponse:
    """返回一条健康度详情。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        result = CaseStore(runtime_settings.case_database_path).fetch_health_score(score_id)
        if not result.rows:
            raise ServiceError("未找到指定健康度记录。", request_id, 404)
        return _health_score_response(tuple(result.rows[0]))
    except ServiceError:
        raise
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


def recalculate_health_scores(
    *,
    settings: Settings | None = None,
) -> dict[str, int]:
    """重算全部健康度（确定性，不耗模型）。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        business_store = DuckDBStore(runtime_settings.database_path)
        business_store.ensure_ready()
        sim = _simulated_data(runtime_settings)
        scores = compute_health_scores(business_store, sim)
        snapshot_id = ""
        try:
            snapshot_id = business_store.get_snapshot().snapshot_id
        except DataAccessError:
            snapshot_id = ""
        computed_at = datetime.now(UTC).isoformat()
        records = [
            HealthScoreWrite(
                id=f"HS_{item['subject_type']}_{item['subject_id']}",
                subject_type=str(item["subject_type"]),
                subject_id=str(item["subject_id"]),
                subject_label=str(item["subject_label"]),
                score=float(item["score"]),
                grade=str(item["grade"]),
                dimension_json=json.dumps(item.get("dimensions", []), ensure_ascii=False),
                drivers_json=json.dumps(item.get("drivers", {}), ensure_ascii=False),
                trend_json=json.dumps(item.get("trend", []), ensure_ascii=False),
                computed_at=computed_at,
                data_snapshot_id=snapshot_id,
            )
            for item in scores
        ]
        case_store = CaseStore(runtime_settings.case_database_path)
        count = case_store.save_health_scores(records)

        # 基于最新健康度生成名单建议
        health_items = [
            {
                "subject_type": r.subject_type,
                "subject_id": r.subject_id,
                "subject_label": r.subject_label,
                "score": r.score,
                "grade": r.grade,
                "drivers": json.loads(r.drivers_json),
                "trend": json.loads(r.trend_json),
            }
            for r in records
            if r.subject_type == "CUSTOMER"
        ]
        current_map = current_list_from_credit(business_store)
        build_recommendations(
            case_store, health_items, current_list_map=current_map, now=computed_at
        )
        return {"count": count}
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc
    except Exception as exc:
        raise ServiceError("健康度计算失败，请检查数据后重试。", request_id, 500) from exc


def _list_recommendation_response(row: tuple[DatabaseScalar, ...]) -> ListRecommendationResponse:
    return ListRecommendationResponse(
        recommendation_id=str(row[0]),
        subject_type=str(row[1]),
        subject_id=str(row[2]),
        subject_label=str(row[3]),
        current_list=str(row[4]),
        target_list=str(row[5]),
        reason=str(row[6]),
        trigger_rule=str(row[7]),
        evidence=json.loads(str(row[8])),
        health_change=str(row[9]),
        risk_amount=_as_float(row[10]),
        review_due_date=str(row[11]),
        status=str(row[12]),
        reviewer=str(row[13]),
        review_reason=str(row[14]),
        review_at=str(row[15]),
        created_at=str(row[16]),
    )


def list_recommendations(
    *,
    status: str | None = None,
    settings: Settings | None = None,
) -> list[ListRecommendationResponse]:
    """返回名单建议列表。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        result = CaseStore(runtime_settings.case_database_path).fetch_list_recommendations(
            status=status
        )
        return [_list_recommendation_response(tuple(row)) for row in result.rows]
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


def review_list_recommendation(
    recommendation_id: str,
    request: ListRecommendationReviewRequest,
    *,
    settings: Settings | None = None,
) -> dict[str, object]:
    """审批/驳回名单建议。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        now = datetime.now(UTC).isoformat()
        return review_recommendation(
            CaseStore(runtime_settings.case_database_path),
            recommendation_id,
            decision=request.decision,
            reviewer=request.reviewer,
            reason=request.reason,
            now=now,
        )
    except KeyError as exc:
        raise ServiceError(str(exc), request_id, 404) from exc
    except ValueError as exc:
        raise ServiceError(str(exc), request_id, 409) from exc
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


def _alert_response(row: tuple[DatabaseScalar, ...]) -> AlertResponse:
    return AlertResponse(
        alert_id=str(row[0]),
        alert_type=str(row[1]),
        subject_type=str(row[2]),
        subject_id=str(row[3]),
        subject_label=str(row[4]),
        severity=str(row[5]),
        message=str(row[6]),
        risk_amount=_as_float(row[7]),
        status=str(row[8]),
        created_at=str(row[9]),
        related_id=str(row[10]),
    )


def list_alerts(
    *,
    status: str | None = None,
    severity: str | None = None,
    settings: Settings | None = None,
) -> list[AlertResponse]:
    """返回预警列表。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        result = CaseStore(runtime_settings.case_database_path).fetch_alerts(
            status=status, severity=severity
        )
        return [_alert_response(tuple(row)) for row in result.rows]
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


def acknowledge_alert(
    alert_id: str,
    *,
    settings: Settings | None = None,
) -> dict[str, object]:
    """确认一条预警。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        ok = CaseStore(runtime_settings.case_database_path).acknowledge_alert(
            alert_id, datetime.now(UTC).isoformat()
        )
        if not ok:
            raise ServiceError("预警不存在或已处理。", request_id, 404)
        return {"ok": True}
    except ServiceError:
        raise
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


def list_sentiments_service(
    *,
    settings: Settings | None = None,
) -> list[SentimentResponse]:
    """返回模拟舆情列表。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        sim = _simulated_data(runtime_settings)
        return [SentimentResponse.model_validate(item) for item in list_sentiments(sim)]
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


def verify_sentiment_service(
    sentiment_id: str,
    request: SentimentVerifyRequest,
    *,
    settings: Settings | None = None,
) -> SentimentResponse:
    """核验舆情并写留痕（通知/预警）。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        sim = _simulated_data(runtime_settings)
        store = CaseStore(runtime_settings.case_database_path)
        now = datetime.now(UTC).isoformat()
        item = verify_sentiment(
            sim,
            sentiment_id,
            decision=request.decision,
            verifier=request.verifier,
            now=now,
            store=store,
        )
        return SentimentResponse.model_validate(item)
    except KeyError as exc:
        raise ServiceError(str(exc), request_id, 404) from exc
    except ValueError as exc:
        raise ServiceError(str(exc), request_id, 409) from exc
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


def list_projects_service(
    *,
    settings: Settings | None = None,
) -> list[ProjectViewResponse]:
    """返回项目类视图：存量合同（真实） + 模拟新项目（P2026-，事前评估入口）。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        business_store = DuckDBStore(runtime_settings.database_path)
        business_store.ensure_ready()
        sim = _simulated_data(runtime_settings)
        existing = [
            ProjectViewResponse.model_validate(item) for item in list_projects(business_store, sim)
        ]
        new_items = [
            ProjectViewResponse(
                project_id=str(item["project_id"]),
                name=str(item["project_name"]),
                customer=str(item["customer_name"]),
                amount_wan=float(str(item["project_amount_wan"])),
                amount_tier=str(item["amount_tier"]),
                stage="立项",
                planned_payment_date=str(item["planned_payment_date"]),
                milestone_progress=0,
                guarantor=str(item["guarantor"]),
                risk_note=str(item["note"]),
                credit_amount_wan=float(str(item["credit_amount_wan"])),
                simulated=True,
            )
            for item in list_new_projects(sim)
        ]
        return [*existing, *new_items]
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


def run_pre_assessment_service(
    project_id: str,
    *,
    settings: Settings | None = None,
) -> PreAssessmentResponse:
    """对模拟新项目执行事前评估。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        business_store = DuckDBStore(runtime_settings.database_path)
        business_store.ensure_ready()
        sim = _simulated_data(runtime_settings)
        item = run_pre_assessment(business_store, sim, project_id)
        return PreAssessmentResponse.model_validate(item)
    except ValueError as exc:
        raise ServiceError(str(exc), request_id, 404) from exc
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


def warning_overview(
    *,
    settings: Settings | None = None,
) -> WarningOverviewResponse:
    """预警总览聚合。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        case_store = CaseStore(runtime_settings.case_database_path)
        health_rows = case_store.fetch_health_scores().rows
        recommendation_rows = case_store.fetch_list_recommendations().rows
        alert_rows = case_store.fetch_alerts().rows
        sim = _simulated_data(runtime_settings)

        grade_distribution: dict[str, int] = {
            "HEALTHY": 0,
            "WATCH": 0,
            "WARNING": 0,
            "HIGH_RISK": 0,
        }
        health_drop_count = 0
        for row in health_rows:
            grade = str(row[5])
            grade_distribution[grade] = grade_distribution.get(grade, 0) + 1
            trend = json.loads(str(row[8]))
            if len(trend) >= 2:
                try:
                    if float(trend[-1]["score"]) < float(trend[-2]["score"]):
                        health_drop_count += 1
                except (KeyError, TypeError, ValueError):
                    pass

        pending_recommendations = [
            _list_recommendation_response(tuple(row))
            for row in recommendation_rows
            if str(row[12]) == "PENDING"
        ]
        open_alerts = [_alert_response(tuple(row)) for row in alert_rows if str(row[8]) == "OPEN"]
        sentiments = list_sentiments(sim)
        open_sentiments = sum(
            1 for item in sentiments if item.get("verify_status") in ("PENDING", "CONFIRMED")
        )
        risk_exposure = sum(_as_float(row[7]) for row in alert_rows if str(row[8]) != "RESOLVED")

        return WarningOverviewResponse(
            pre_assessment_pending=len(sim.new_projects),
            in_process_alerts=sum(
                1 for row in alert_rows if str(row[1]) == "IN_PROCESS" and str(row[8]) == "OPEN"
            ),
            health_drop_count=health_drop_count,
            pending_list_recommendations=len(pending_recommendations),
            open_sentiments=open_sentiments,
            high_risk_count=grade_distribution.get("HIGH_RISK", 0),
            risk_exposure=risk_exposure,
            grade_distribution=grade_distribution,
            pending_recommendations=pending_recommendations,
            open_alerts=open_alerts,
        )
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc

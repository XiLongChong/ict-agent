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
    stream_investigation_agent,
)
from ict_agent.config import ConfigurationError, Settings, load_settings
from ict_agent.data import (
    CaseStore,
    DataAccessError,
    DatabaseScalar,
    DuckDBStore,
    InvestigationWrite,
    ReviewWrite,
)
from ict_agent.models import (
    CaseStatus,
    CaseType,
    DashboardResponse,
    InvestigationRecord,
    InvestigationStreamEvent,
    ReviewDecision,
    ReviewRecord,
    ReviewRequest,
    RiskCaseDetail,
    RiskCaseSummary,
    RiskOverviewResponse,
    RiskPriority,
    RuleHit,
    RuleRunResponse,
)
from ict_agent.rules import build_rule_scan
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


def _as_int(value: DatabaseScalar) -> int:
    if value is None:
        return 0
    return int(value)


def _as_float(value: DatabaseScalar) -> float:
    if value is None:
        return 0.0
    return float(value)


def _case_summary(row: tuple[DatabaseScalar, ...]) -> RiskCaseSummary:
    return RiskCaseSummary(
        case_id=str(row[0]),
        case_type=cast(CaseType, str(row[1])),
        entity_type=str(row[2]),
        entity_id=str(row[3]),
        entity_label=str(row[4]),
        observation_date=str(row[5]).split("T", maxsplit=1)[0],
        status=cast(CaseStatus, str(row[6])),
        priority=cast(RiskPriority, str(row[7])),
        exposure_amount=_as_float(row[8]),
        summary=str(row[9]),
        rule_hit_count=_as_int(row[10]),
        rule_set_version=str(row[11]),
        updated_at=str(row[12]),
        next_review_at=None if row[13] is None else str(row[13]).split("T", maxsplit=1)[0],
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
            open_cases=_as_int(overview_row[1]),
            pending_review_cases=_as_int(overview_row[2]),
            monitoring_cases=_as_int(overview_row[3]),
            action_required_cases=_as_int(overview_row[4]),
            critical_cases=_as_int(overview_row[5]),
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
                severity=cast(RiskPriority, str(hit[4])),
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
                action=None if review[5] is None else str(review[5]),
                next_review_at=(
                    None if review[6] is None else str(review[6]).split("T", maxsplit=1)[0]
                ),
                created_at=str(review[7]),
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
        DuckDBStore(runtime_settings.database_path).ensure_ready()
        return PreparedInvestigation(settings=runtime_settings, case=case, model=model)
    except ServiceError:
        raise
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
    yield InvestigationStreamEvent(
        sequence=sequence,
        event_type="RUN_STARTED",
        message="已载入案件，DeepSeek 高强度思考正在发现数据并调查证据。",
    )
    try:
        async for event in stream_investigation_agent(
            prepared.settings, prepared.case, model=prepared.model
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
    status_by_decision: dict[str, CaseStatus] = {
        "MONITOR": "MONITORING",
        "ACTION_REQUIRED": "ACTION_REQUIRED",
        "FALSE_POSITIVE": "CLOSED_FALSE_POSITIVE",
        "RESOLVED": "CLOSED_RESOLVED",
    }
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        store = CaseStore(runtime_settings.case_database_path)
        if not store.fetch_case(case_id).rows:
            raise ServiceError("未找到指定风险案件。", request_id, 404)
        created_at = datetime.now(UTC).isoformat()
        record = ReviewWrite(
            review_id=uuid4().hex,
            case_id=case_id,
            decision=request.decision,
            reviewer=request.reviewer,
            reason=request.reason,
            action=request.action,
            next_review_at=(
                request.next_review_at.isoformat() if request.next_review_at is not None else None
            ),
            created_at=created_at,
        )
        store.save_review(record, status_by_decision[request.decision])
        return ReviewRecord(
            review_id=record.review_id,
            case_id=record.case_id,
            decision=request.decision,
            reviewer=record.reviewer,
            reason=record.reason,
            action=record.action,
            next_review_at=record.next_review_at,
            created_at=record.created_at,
        )
    except ServiceError:
        raise
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc

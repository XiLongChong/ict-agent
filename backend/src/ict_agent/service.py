"""经营分析、风险案件、调查事件流与人工审核的应用服务。"""

from __future__ import annotations

import json
import logging
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import cast
from uuid import NAMESPACE_URL, uuid4, uuid5

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
    CaseWrite,
    DataAccessError,
    DatabaseScalar,
    DuckDBStore,
    InvestigationWrite,
    PreTransactionWrite,
    ReviewWrite,
    RuleHitWrite,
)
from ict_agent.feishu import (
    CaseNotification,
    CaseNotificationEvent,
    FeishuIntegrationError,
    RuleScanNotification,
    get_feishu_status,
    send_feishu_case_notification,
    send_feishu_rule_scan_notification,
    send_feishu_test_card,
)
from ict_agent.models import (
    BusinessType,
    CaseStatus,
    CaseType,
    DashboardResponse,
    DataQualityStatus,
    DataSnapshotResponse,
    DataSourceSnapshot,
    DiscoverySource,
    FeishuStatusResponse,
    FeishuTestResponse,
    GeneratedSimulationScenario,
    InvestigationCaseInput,
    InvestigationDataQuality,
    InvestigationRecord,
    InvestigationSignalInput,
    InvestigationStreamEvent,
    PreTransactionSimulationRequest,
    PreTransactionSimulationResponse,
    ReviewDecision,
    ReviewRecord,
    ReviewRequest,
    RiskCaseDetail,
    RiskCaseSummary,
    RiskOverviewResponse,
    RiskPriority,
    RuleRunResponse,
)
from ict_agent.pretransaction import Scenario, generate_simulated_order
from ict_agent.rules import build_rule_scan
from ict_agent.tools import (
    AnalysisInputError,
    get_ar_trend,
    get_business_overview,
    get_customer_credit_context,
    get_historical_order_profile,
    get_inventory_health,
    get_latest_ar_summary,
    list_customer_business_segments,
)

logger = logging.getLogger(__name__)


class ServiceError(RuntimeError):
    """可安全映射到 HTTP 的应用错误。"""

    def __init__(self, message: str, request_id: str, status_code: int) -> None:
        super().__init__(message)
        self.request_id = request_id
        self.status_code = status_code


async def _notify_case_event(
    event_type: CaseNotificationEvent,
    case: RiskCaseDetail,
    settings: Settings,
    *,
    detail: str = "",
) -> None:
    """发送并审计飞书案件通知；外部通道失败不回滚核心业务事务。"""

    if settings.feishu_app_id is None or settings.feishu_app_secret is None:
        return
    created_at = datetime.now(UTC).isoformat()
    notification_id = uuid5(
        NAMESPACE_URL,
        f"feishu:{event_type}:{case.case_id}:{case.status}:{case.updated_at}",
    ).hex
    status = "SENT"
    message_id = ""
    error_message = ""
    try:
        message_id = await send_feishu_case_notification(
            CaseNotification(
                event_type=event_type,
                case_id=case.case_id,
                case_type=case.case_type,
                entity_label=case.entity_label,
                priority=case.priority,
                status=case.status,
                summary=case.summary,
                business_type=case.business_type,
                observation_date=case.observation_date,
                exposure_amount=case.exposure_amount,
                detail=detail,
                public_base_url=settings.public_base_url,
            )
        )
    except FeishuIntegrationError as exc:
        status = "FAILED"
        error_message = str(exc)
        logger.warning("飞书案件通知失败：case_id=%s event=%s", case.case_id, event_type)
    CaseStore(settings.case_database_path).save_feishu_notification(
        notification_id=notification_id,
        event_type=event_type,
        case_id=case.case_id,
        status=status,
        message_id=message_id,
        error_message=error_message,
        created_at=created_at,
    )


async def _notify_rule_scan(response: RuleRunResponse, settings: Settings) -> None:
    """规则扫描只发送一张聚合卡片，避免首次扫描产生通知风暴。"""

    if settings.feishu_app_id is None or settings.feishu_app_secret is None:
        return
    created_at = datetime.now(UTC).isoformat()
    notification_id = uuid5(NAMESPACE_URL, f"feishu:scan:{response.run_id}").hex
    status = "SENT"
    message_id = ""
    error_message = ""
    try:
        message_id = await send_feishu_rule_scan_notification(
            RuleScanNotification(
                run_id=response.run_id,
                observation_date=response.observation_date,
                cases_detected=response.cases_detected,
                cases_created=response.cases_created,
                signal_count=response.rule_hits,
                public_base_url=settings.public_base_url,
            )
        )
    except FeishuIntegrationError as exc:
        status = "FAILED"
        error_message = str(exc)
        logger.warning("飞书规则扫描通知未发送：%s", exc)
    CaseStore(settings.case_database_path).save_feishu_notification(
        notification_id=notification_id,
        event_type="RULE_SCAN_COMPLETED",
        case_id=response.run_id,
        status=status,
        message_id=message_id,
        error_message=error_message,
        created_at=created_at,
    )


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
    business_type = str(row[6]) if row[6] is not None else None
    return RiskCaseSummary(
        case_id=str(row[0]),
        discovery_source=cast(DiscoverySource, str(row[1])),
        case_type=cast(CaseType, str(row[2])),
        entity_type=str(row[3]),
        entity_id=str(row[4]),
        entity_label=str(row[5]),
        business_type=cast(BusinessType, business_type) if business_type else None,
        observation_date=str(row[7]).split("T", maxsplit=1)[0],
        status=_case_status(row[8]),
        priority=_risk_priority(row[9]),
        exposure_amount=_as_float(row[10]),
        summary=str(row[11]),
        signal_overview=str(row[18]),
        signal_count=_as_int(row[12]),
        source_set_version=str(row[13]),
        source_snapshot_id=str(row[14]),
        data_quality=InvestigationDataQuality(
            status=cast(DataQualityStatus, str(row[15])),
            warnings=json.loads(str(row[16])),
        ),
        updated_at=str(row[17]),
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


async def run_rule_scan(*, settings: Settings | None = None) -> RuleRunResponse:
    """执行一次确定性规则扫描并幂等写入案件库。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        business_store = DuckDBStore(runtime_settings.database_path)
        business_store.ensure_ready()
        draft = build_rule_scan(business_store)
        snapshot_id = business_store.get_snapshot().snapshot_id
        segments = list_customer_business_segments(business_store)
        types_by_customer: dict[str, set[BusinessType]] = {}
        for customer_id, _customer_name, business_type, _count in segments:
            types_by_customer.setdefault(customer_id, set()).add(business_type)
        cases = []
        for case in draft.cases:
            available_types = sorted(types_by_customer.get(case.entity_id, set()))
            case_business_type = available_types[0] if len(available_types) == 1 else None
            context = dict(case.entity_context)
            if available_types:
                context["available_business_types"] = ",".join(available_types)
            cases.append(
                replace(
                    case,
                    entity_context=context,
                    business_type=case_business_type,
                    source_snapshot_id=snapshot_id,
                    data_quality_status="PASS",
                    data_quality_warnings=(),
                )
            )
        run = replace(draft.run, source_snapshot_id=snapshot_id)
        case_store = CaseStore(runtime_settings.case_database_path)
        created_case_ids = case_store.save_rule_scan(run, cases, draft.hits)
        response = RuleRunResponse(
            run_id=run.run_id,
            rule_set_version=run.rule_set_version,
            observation_date=run.observation_date,
            cases_detected=run.cases_detected,
            cases_created=len(created_case_ids),
            rule_hits=run.rule_hits,
            receivable_cases=run.receivable_cases,
            inventory_cases=run.inventory_cases,
            created_at=run.created_at,
        )
        await _notify_rule_scan(response, runtime_settings)
        return response
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
                "PRE_TRANSACTION": _as_int(overview_row[9]),
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
                row[5],
                row[6],
                row[8],
                row[9],
                row[10],
                row[11],
                row[12],
                row[13],
                row[14],
                row[15],
                row[16],
                row[17],
                row[18],
                row[19],
            )
        )
        signals = [
            InvestigationSignalInput(
                signal_id=str(signal[0]),
                signal_code=str(signal[1]),
                signal_name=str(signal[2]),
                source_version=str(signal[3]),
                severity=_risk_priority(signal[4]),
                exposure_amount=_as_float(signal[5]),
                reason=str(signal[6]),
                metrics=json.loads(str(signal[7])),
                threshold_source=str(signal[8]),
                threshold_version=str(signal[9]),
                sources=json.loads(str(signal[10])),
                period=str(signal[11]),
            )
            for signal in store.fetch_signals(case_id).rows
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
                protocol=(
                    json.loads(str(investigation[5])) if investigation[5] is not None else None
                ),
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
            entity_context=json.loads(str(row[7])),
            signals=signals,
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
        if case.data_quality.status == "FAIL":
            raise ServiceError(
                "案件数据质量未通过，必须先修复数据后才能启动 Agent 调查。",
                request_id,
                409,
            )
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
    if outcome.protocol is None:
        raise RuntimeError("调查已完成但缺少最后一轮模型协议。")
    created_at = datetime.now(UTC).isoformat()
    record = InvestigationRecord(
        investigation_id=uuid4().hex,
        case_id=prepared.case.case_id,
        report=outcome.report,
        evidence=outcome.evidence,
        protocol=outcome.protocol,
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
            protocol_json=outcome.protocol.model_dump_json(),
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
            updated_case = get_case_detail(prepared.case.case_id, settings=prepared.settings)
            await _notify_case_event(
                "PARTIAL_REPORT" if event.partial else "INVESTIGATION_COMPLETED",
                updated_case,
                prepared.settings,
                detail=record.report.investigation_summary,
            )
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


async def review_case(
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
        if str(case_rows[0][9]) != "PENDING_HUMAN_REVIEW":
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
        new_status = status_by_decision[request.decision]
        store.save_review(record, new_status)
        response = ReviewRecord(
            review_id=record.review_id,
            case_id=record.case_id,
            decision=request.decision,
            reviewer=record.reviewer,
            reason=record.reason,
            created_at=record.created_at,
        )
        updated_case = get_case_detail(case_id, settings=runtime_settings)
        await _notify_case_event(
            "REVIEW_COMPLETED",
            updated_case,
            runtime_settings,
            detail=f"{request.reviewer}：{request.reason}",
        )
        return response
    except ServiceError:
        raise
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


def get_feishu_status_service(*, settings: Settings | None = None) -> FeishuStatusResponse:
    """返回不含密钥的飞书机器人运行状态。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        status = get_feishu_status(runtime_settings)
        return FeishuStatusResponse(**status.__dict__)
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


async def send_feishu_test_service() -> FeishuTestResponse:
    """向当前绑定群发送一张飞书测试卡片。"""

    request_id = uuid4().hex
    try:
        message_id = await send_feishu_test_card()
        return FeishuTestResponse(sent=True, message_id=message_id)
    except FeishuIntegrationError as exc:
        raise ServiceError(str(exc), request_id, 409) from exc


def _simulation_response(row: tuple[DatabaseScalar, ...]) -> PreTransactionSimulationResponse:
    return PreTransactionSimulationResponse(
        simulation_id=str(row[0]),
        case_id=str(row[1]),
        customer_id=str(row[2]),
        customer_name=str(row[3]),
        business_type=cast(BusinessType, str(row[4])),
        amount_yuan=_as_float(row[5]),
        proposed_term_days=_as_int(row[6]),
        expected_margin_rate=_as_float(row[7]) if row[7] is not None else None,
        scenario=cast(GeneratedSimulationScenario, str(row[8])),
        seed=_as_int(row[9]),
        historical_order_count=_as_int(row[10]),
        distribution_summary=json.loads(str(row[11])),
        source_snapshot_id=str(row[12]),
        data_quality_status=cast(DataQualityStatus, str(row[13])),
        data_quality_warnings=json.loads(str(row[14])),
        generated_at=str(row[15]),
        simulated=True,
    )


def list_pre_transaction_simulations(
    *,
    limit: int = 50,
    settings: Settings | None = None,
) -> list[PreTransactionSimulationResponse]:
    """返回最近的模拟交易及其统一案件编号。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        result = CaseStore(runtime_settings.case_database_path).fetch_pre_transaction_simulations(
            limit=limit
        )
        return [_simulation_response(tuple(row)) for row in result.rows]
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc


async def create_pre_transaction_simulation(
    request: PreTransactionSimulationRequest,
    *,
    settings: Settings | None = None,
) -> PreTransactionSimulationResponse:
    """基于真实历史分布生成模拟交易，并进入统一案件与Agent调查流程。"""

    request_id = uuid4().hex
    try:
        runtime_settings = settings or load_settings(require_api_key=False, require_data_dir=False)
        business_store = DuckDBStore(runtime_settings.database_path)
        business_store.ensure_ready()
        actual_seed = (
            request.seed
            if request.seed is not None
            else random.SystemRandom().randrange(2_147_483_648)
        )
        candidates = list_customer_business_segments(
            business_store,
            customer_id=request.customer_id,
            business_type=request.business_type,
        )
        if not candidates:
            raise ServiceError("没有找到符合条件且具备正向历史订单的客户业务。", request_id, 404)
        customer_id, _customer_name, business_type, _count = random.Random(actual_seed).choice(
            candidates
        )
        profile = get_historical_order_profile(business_store, customer_id, business_type)
        simulated = generate_simulated_order(
            profile,
            Scenario(request.scenario),
            seed=actual_seed,
        )
        credit_context = get_customer_credit_context(business_store, customer_id)
        list_status = next(
            (str(row[1]) for row in credit_context.rows if str(row[0]) == "名单状态"),
            "未知",
        )
        priority = cast(
            RiskPriority,
            {
                Scenario.NORMAL: "LOW",
                Scenario.BORDERLINE: "MEDIUM",
                Scenario.ANOMALY: "HIGH",
            }[simulated.scenario],
        )
        reason = (
            "新交易在成交前进入Agent基线调查。"
            if simulated.scenario is Scenario.NORMAL
            else (
                "拟交易金额处于客户同业务历史分布的偏高区间，需要核对回款和敞口。"
                if simulated.scenario is Scenario.BORDERLINE
                else "拟交易金额显著高于客户同业务历史P90，需要在成交前调查。"
            )
        )
        if list_status == "黑名单":
            priority = "HIGH"
            reason += " 当前授信主数据标记为黑名单，必须人工复核。"
        case_id = f"pre_{simulated.simulation_id.replace('-', '')[:20]}"
        generated_date = simulated.generated_at.split("T", maxsplit=1)[0]
        context: dict[str, DatabaseScalar] = {
            "simulation_id": simulated.simulation_id,
            "customer_id": simulated.customer_id,
            "customer_name": simulated.customer_name,
            "business_type": simulated.business_type,
            "amount_yuan": simulated.amount_yuan,
            "proposed_term_days": simulated.proposed_term_days,
            "expected_margin_rate": simulated.expected_margin_rate,
            "scenario": simulated.scenario.value,
            "historical_order_count": simulated.historical_order_count,
            "historical_median_amount_yuan": simulated.distribution_summary["median_yuan"],
            "historical_p90_amount_yuan": simulated.distribution_summary["p90_yuan"],
            "list_status_at_intake": list_status,
            "generated_at": simulated.generated_at,
            "simulated": True,
        }
        source_version = "pre-transaction-simulator-1.0"
        case = CaseWrite(
            case_id=case_id,
            case_type="PRE_TRANSACTION",
            entity_type="CUSTOMER",
            entity_id=simulated.customer_id,
            entity_label=f"{simulated.customer_id} {simulated.customer_name}",
            entity_context=context,
            observation_date=generated_date,
            priority=priority,
            exposure_amount=simulated.amount_yuan,
            summary=reason,
            rule_hit_count=1,
            rule_set_version=source_version,
            created_at=simulated.generated_at,
            discovery_source="PRE_TRANSACTION",
            business_type=simulated.business_type,
            source_snapshot_id=simulated.source_snapshot_id,
            data_quality_status=simulated.data_quality_status,
            data_quality_warnings=tuple(simulated.warnings),
        )
        signal = RuleHitWrite(
            rule_hit_id=f"sig_{simulated.simulation_id.replace('-', '')[:20]}",
            case_id=case_id,
            rule_id="PRE_TRANSACTION_REVIEW",
            rule_name="新交易事前调查",
            rule_version=source_version,
            severity=priority,
            exposure_amount=simulated.amount_yuan,
            reason=reason,
            metrics={
                "proposed_amount_yuan": simulated.amount_yuan,
                "historical_median_yuan": simulated.distribution_summary["median_yuan"],
                "historical_p90_yuan": simulated.distribution_summary["p90_yuan"],
                "scenario": simulated.scenario.value,
                "historical_order_count": simulated.historical_order_count,
                "list_status_at_intake": list_status,
            },
            threshold_source="客户同业务类型历史分布与成交前必查流程",
            threshold_version=simulated.source_snapshot_id,
            sources=("sales", "payments", "customer_credit"),
            period=generated_date,
        )
        write = PreTransactionWrite(
            simulation_id=simulated.simulation_id,
            case_id=case_id,
            customer_id=simulated.customer_id,
            customer_name=simulated.customer_name,
            business_type=simulated.business_type,
            amount_yuan=simulated.amount_yuan,
            proposed_term_days=simulated.proposed_term_days,
            expected_margin_rate=simulated.expected_margin_rate,
            scenario=simulated.scenario.value,
            seed=simulated.seed,
            historical_order_count=simulated.historical_order_count,
            distribution_json=json.dumps(simulated.distribution_summary, ensure_ascii=False),
            source_snapshot_id=simulated.source_snapshot_id,
            data_quality_status=simulated.data_quality_status,
            data_quality_warnings_json=json.dumps(simulated.warnings, ensure_ascii=False),
            generated_at=simulated.generated_at,
        )
        case_store = CaseStore(runtime_settings.case_database_path)
        created = case_store.save_pre_transaction_case(case, signal, write)
        if created:
            await _notify_case_event(
                "CASE_CREATED",
                get_case_detail(case_id, settings=runtime_settings),
                runtime_settings,
                detail="模拟交易已进入成交前Agent调查流程。",
            )
        result = case_store.fetch_pre_transaction_simulations(limit=200)
        row = next(
            (item for item in result.rows if str(item[0]) == simulated.simulation_id),
            None,
        )
        if row is None:
            raise DataAccessError("模拟交易保存后无法读取。")
        return _simulation_response(tuple(row))
    except ServiceError:
        raise
    except AnalysisInputError as exc:
        raise ServiceError(str(exc), request_id, 422) from exc
    except (ConfigurationError, DataAccessError) as exc:
        raise ServiceError(str(exc), request_id, 503) from exc

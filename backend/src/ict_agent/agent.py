"""DeepSeek Provider、Pydantic AI 动态调查内核与受控工具注册。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic_ai import Agent, AgentRunResultEvent, ModelRetry, RunContext, UsageLimits
from pydantic_ai.messages import FunctionToolCallEvent, FunctionToolResultEvent, ToolReturnPart
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.deepseek import DeepSeekProvider

from ict_agent import tools as analysis_tools
from ict_agent.config import OFFICIAL_DEEPSEEK_BASE_URL, Settings
from ict_agent.data import DuckDBStore
from ict_agent.models import (
    BusinessDataCatalog,
    BusinessRecordSearchQuery,
    CaseType,
    Evidence,
    EvidenceQuery,
    InvestigationCaseInput,
    InvestigationFact,
    InvestigationHypothesis,
    InvestigationReport,
    InvestigationStreamEventType,
    InvestigationToolName,
    InvestigationTraceEvent,
    InvestigationTraceType,
    JsonScalar,
    RiskCaseDetail,
    RiskSignalAssessment,
    ToolResult,
)
from ict_agent.prompts import INVESTIGATION_INSTRUCTIONS
from ict_agent.semantic import time_window_covers

logger = logging.getLogger(__name__)

INVESTIGATION_TOOLS: tuple[InvestigationToolName, ...] = (
    "discover_evidence_capabilities",
    "search_business_records",
    "query_business_evidence",
)
AR_CORE_EVIDENCE = {
    ("receivables", "month"),
    ("receivables", "order"),
    ("sales_payments", "month"),
}
INVENTORY_CORE_EVIDENCE = {
    ("inventory", "quarter"),
    ("inventory", "age_bucket"),
    ("sales", "month"),
}
PRE_TRANSACTION_CORE_EVIDENCE = {
    ("proposal", "order"),
    ("customer_profile", "business_type"),
    ("receivables", "month"),
    ("sales_payments", "month"),
    ("credit", "customer"),
}


def allowed_investigation_tools(case_type: CaseType) -> tuple[InvestigationToolName, ...]:
    """应收与库存共用同一组受治理调查工具。"""

    del case_type
    return INVESTIGATION_TOOLS


@dataclass
class InvestigationDependencies:
    """单次案件调查的数据地图、证据和审计轨迹。"""

    store: DuckDBStore
    case: InvestigationCaseInput
    catalog_discovered: bool = False
    evidence: list[Evidence] = field(default_factory=list)
    called_tools: set[InvestigationToolName] = field(default_factory=set)
    query_signatures: set[str] = field(default_factory=set)
    query_history: list[EvidenceQuery] = field(default_factory=list)
    search_signatures: set[str] = field(default_factory=set)
    trace: list[InvestigationTraceEvent] = field(default_factory=list)


@dataclass(frozen=True)
class InvestigationOutcome:
    """结构化调查报告及本轮实际工具证据。"""

    report: InvestigationReport
    evidence: list[Evidence]
    partial: bool = False
    usage: dict[str, int | float | str | None] | None = None
    called_tools: tuple[InvestigationToolName, ...] = ()


@dataclass(frozen=True)
class InvestigationAgentProgress:
    """从 Pydantic AI 事件中提炼的前端可观察进度。"""

    event_type: InvestigationStreamEventType
    message: str
    tool_name: InvestigationToolName | None = None
    evidence: Evidence | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _trace(
    dependencies: InvestigationDependencies,
    event_type: InvestigationTraceType,
    title: str,
    detail: str,
    *,
    tool_name: InvestigationToolName | None = None,
    evidence_id: str | None = None,
) -> None:
    dependencies.trace.append(
        InvestigationTraceEvent(
            event_type=event_type,
            title=title,
            detail=detail,
            tool_name=tool_name,
            evidence_id=evidence_id,
            created_at=_now(),
        )
    )


def _record_investigation_evidence(
    dependencies: InvestigationDependencies,
    tool_name: InvestigationToolName,
    result: ToolResult,
    *,
    arguments: dict[str, JsonScalar | list[JsonScalar]],
) -> ToolResult:
    evidence_id = uuid4().hex
    evidence = Evidence(
        evidence_id=evidence_id,
        tool_name=tool_name,
        arguments=arguments,
        sources=result.sources,
        period=result.period,
        summary=result.summary,
        columns=result.columns,
        rows=result.rows,
        metric_definitions=result.metric_definitions,
        warnings=result.warnings,
    )
    dependencies.evidence.append(evidence)
    dependencies.called_tools.add(tool_name)
    _trace(
        dependencies,
        "TOOL_COMPLETED",
        "证据已取得",
        result.summary,
        tool_name=tool_name,
        evidence_id=evidence_id,
    )
    return result.model_copy(update={"evidence_id": evidence_id})


def _evidence_query_keys(dependencies: InvestigationDependencies) -> set[tuple[str, str]]:
    return {
        (str(item.arguments.get("dataset")), str(item.arguments.get("grain")))
        for item in dependencies.evidence
        if item.tool_name == "query_business_evidence"
    }


def _required_evidence(dependencies: InvestigationDependencies) -> set[tuple[str, str]]:
    if dependencies.case.case_type == "INVENTORY":
        return set(INVENTORY_CORE_EVIDENCE)
    if dependencies.case.case_type == "PRE_TRANSACTION":
        return set(PRE_TRANSACTION_CORE_EVIDENCE)
    required = set(AR_CORE_EVIDENCE)
    signal_codes = {item.signal_code for item in dependencies.case.signals}
    if "AR_OPERATING_DEEP_OVERDUE" in signal_codes:
        required.update({("extensions", "order"), ("credit", "customer")})
    elif "AR_OPERATING_EXPOSURE_BUILDUP" in signal_codes:
        required.update({("contracts", "contract"), ("credit", "customer")})
    else:
        required.add(("credit", "customer"))
    return required


def _missing_requirements(dependencies: InvestigationDependencies) -> list[str]:
    missing: list[str] = []
    if not dependencies.catalog_discovered:
        missing.append("尚未发现可用业务数据")
    query_keys = _evidence_query_keys(dependencies)
    for dataset, grain in sorted(_required_evidence(dependencies) - query_keys):
        missing.append(f"缺少必要证据：{dataset}/{grain}")
    return missing


def _minimum_evidence_ready(dependencies: InvestigationDependencies) -> bool:
    return not _missing_requirements(dependencies)


def _query_is_redundant(previous_queries: Sequence[EvidenceQuery], query: EvidenceQuery) -> bool:
    """拒绝已被同范围、更宽指标和更多行完整覆盖的语义查询。"""

    return any(
        previous.dataset == query.dataset
        and previous.grain == query.grain
        and time_window_covers(previous.time_window, query.time_window)
        and previous.sort_by == query.sort_by
        and previous.sort_direction == query.sort_direction
        and previous.limit >= query.limit
        and set(previous.metrics) >= set(query.metrics)
        for previous in previous_queries
    )


def build_investigation_case_input(case: RiskCaseDetail) -> InvestigationCaseInput:
    """把统一案件存储模型映射为冻结的 V3 调查输入契约。"""

    return InvestigationCaseInput(
        case_id=case.case_id,
        discovery_source=case.discovery_source,
        case_type=case.case_type,
        entity_type=case.entity_type,
        entity_id=case.entity_id,
        entity_label=case.entity_label,
        business_type=case.business_type,
        entity_context=case.entity_context,
        observation_date=case.observation_date,
        priority=case.priority,
        exposure_amount=case.exposure_amount,
        summary=case.summary,
        source_set_version=case.source_set_version,
        source_snapshot_id=case.source_snapshot_id,
        signals=case.signals,
        data_quality=case.data_quality,
    )


def _create_model(settings: Settings, model: Model | None) -> Model:
    if model is not None:
        return model
    if settings.deepseek_base_url != OFFICIAL_DEEPSEEK_BASE_URL:
        raise ValueError("当前 Agent 只允许使用 DeepSeek 官方 Provider。")
    if settings.deepseek_api_key is None:
        raise ValueError("缺少 DeepSeek API Key。")
    provider = DeepSeekProvider(api_key=settings.deepseek_api_key.get_secret_value())
    return OpenAIChatModel(settings.deepseek_model, provider=provider)


def _create_investigation_agent(
    settings: Settings,
    model: Model | None = None,
) -> Agent[InvestigationDependencies, InvestigationReport]:
    """创建只暴露发现、搜索和查询三项受治理能力的调查 Agent。"""

    agent = Agent[InvestigationDependencies, InvestigationReport](
        _create_model(settings, model),
        output_type=InvestigationReport,
        deps_type=InvestigationDependencies,
        instructions=INVESTIGATION_INSTRUCTIONS,
        model_settings=OpenAIChatModelSettings(
            max_tokens=5_000,
            thinking="high",
            parallel_tool_calls=False,
        ),
        retries=3,
    )

    @agent.tool(sequential=True)
    def discover_evidence_capabilities(
        ctx: RunContext[InvestigationDependencies],
    ) -> BusinessDataCatalog:
        """发现当前案件真实可用的数据集、粒度、指标、窗口和限制。"""

        if ctx.deps.catalog_discovered:
            raise ModelRetry("证据能力已经发现，请直接查询下一项必要证据。")
        catalog = analysis_tools.discover_evidence_capabilities(
            ctx.deps.store,
            ctx.deps.case.case_type,
            ctx.deps.case.entity_context,
            ctx.deps.case.observation_date,
        )
        ctx.deps.catalog_discovered = True
        ctx.deps.called_tools.add("discover_evidence_capabilities")
        return catalog

    @agent.tool(sequential=True)
    def search_business_records(
        ctx: RunContext[InvestigationDependencies], search: BusinessRecordSearchQuery
    ) -> ToolResult:
        """在当前案件关联记录中按业务标识搜索客户、合同、订单或物料。"""

        if not ctx.deps.catalog_discovered:
            raise ModelRetry("必须先调用 discover_evidence_capabilities。")
        signature = search.model_dump_json()
        if signature in ctx.deps.search_signatures:
            raise ModelRetry("相同业务记录搜索已经执行，请使用已有结果或调整关键词。")
        result = analysis_tools.search_business_records(
            ctx.deps.store,
            ctx.deps.case.case_type,
            ctx.deps.case.entity_context,
            search,
        )
        ctx.deps.search_signatures.add(signature)
        ctx.deps.called_tools.add("search_business_records")
        return result

    @agent.tool(sequential=True)
    def query_business_evidence(
        ctx: RunContext[InvestigationDependencies], query: EvidenceQuery
    ) -> ToolResult:
        """在案件主体范围内按注册的数据集、粒度、指标和窗口查询证据。"""

        if not ctx.deps.catalog_discovered:
            raise ModelRetry("必须先调用 discover_evidence_capabilities 了解可用能力。")
        signature = query.model_dump_json()
        if signature in ctx.deps.query_signatures or _query_is_redundant(
            ctx.deps.query_history, query
        ):
            raise ModelRetry(
                "该查询已被已有证据完整覆盖，请直接使用已有 evidence_id，不要重复取数。"
            )
        try:
            result = analysis_tools.query_business_evidence(
                ctx.deps.store,
                ctx.deps.case.case_type,
                ctx.deps.case.entity_context,
                query,
            )
        except analysis_tools.AnalysisInputError as exc:
            raise ModelRetry(f"受控查询参数不受支持：{exc} 请根据能力目录调整查询。") from exc
        ctx.deps.query_signatures.add(signature)
        ctx.deps.query_history.append(query)
        return _record_investigation_evidence(
            ctx.deps,
            "query_business_evidence",
            result,
            arguments=query.model_dump(mode="json"),
        )

    @agent.output_validator
    def validate_investigation_report(
        ctx: RunContext[InvestigationDependencies], report: InvestigationReport
    ) -> InvestigationReport:
        """拒绝基础证据不足、假引用、无依据状态和高风险幻觉表述。"""

        missing = _missing_requirements(ctx.deps)
        if missing:
            raise ModelRetry(f"调查尚未达到最低证据覆盖：{missing}。请继续取证后再报告。")
        valid_ids = {item.evidence_id for item in ctx.deps.evidence}
        invalid_risk_ids = set(report.risk_assessment.evidence_ids) - valid_ids
        if invalid_risk_ids:
            raise ModelRetry(f"风险判断引用了不存在的证据编号：{sorted(invalid_risk_ids)}。")
        if not report.facts:
            raise ModelRetry("报告必须至少列出一条由工具直接证明的数据事实。")
        if ctx.deps.case.data_quality.status in ("WARNING", "UNKNOWN") and not report.limitations:
            raise ModelRetry("案件数据质量不是 PASS，报告必须在 limitations 中保留数据限制。")
        for fact in report.facts:
            if not fact.evidence_ids:
                raise ModelRetry(f"事实“{fact.statement}”没有引用 evidence_id。")
            invalid_ids = set(fact.evidence_ids) - valid_ids
            if invalid_ids:
                raise ModelRetry(f"事实引用了不存在的证据编号：{sorted(invalid_ids)}。")
        for hypothesis in report.hypotheses:
            refs = set(hypothesis.supporting_evidence_ids) | set(
                hypothesis.contradicting_evidence_ids
            )
            overlap = set(hypothesis.supporting_evidence_ids) & set(
                hypothesis.contradicting_evidence_ids
            )
            if overlap:
                raise ModelRetry(
                    f"假设“{hypothesis.statement}”把同一证据同时列为支持和反驳："
                    f"{sorted(overlap)}。请按证据实际方向保留一侧。"
                )
            invalid_ids = refs - valid_ids
            if invalid_ids:
                raise ModelRetry(f"假设引用了不存在的证据编号：{sorted(invalid_ids)}。")
            if hypothesis.status == "SUPPORTED" and not hypothesis.supporting_evidence_ids:
                raise ModelRetry(f"SUPPORTED 假设“{hypothesis.statement}”缺少支持证据。")
            if hypothesis.status == "WEAKENED" and not hypothesis.contradicting_evidence_ids:
                raise ModelRetry(f"WEAKENED 假设“{hypothesis.statement}”缺少反驳证据。")
            if (
                hypothesis.status == "UNRESOLVED"
                and not hypothesis.missing_evidence
                and not (
                    hypothesis.supporting_evidence_ids and hypothesis.contradicting_evidence_ids
                )
            ):
                raise ModelRetry(
                    f"UNRESOLVED 假设“{hypothesis.statement}”必须说明缺失证据或证据冲突。"
                )
        unsupported_definitive_claims = (
            "已确认坏账",
            "已经形成坏账",
            "属于坏账",
            "确定为坏账",
            "确定无法回收",
            "已经无法回收",
            "必然无法回收",
            "确认不可回收",
            "已停供",
            "已经停供",
            "停止供货",
            "客户无回款能力",
            "丧失回款能力",
            "已进入诉讼程序",
        )
        abstention_markers = (
            "无法判断",
            "不能判断",
            "没有证据",
            "证据不足",
            "尚无数据",
            "不等于",
            "不能断言",
            "不得断言",
            "未确认",
        )
        claim_texts = [report.investigation_summary, report.risk_assessment.statement]
        claim_texts.extend(report.risk_assessment.drivers)
        claim_texts.extend(report.risk_assessment.counter_signals)
        claim_texts.extend(fact.statement for fact in report.facts)
        claim_texts.extend(hypothesis.statement for hypothesis in report.hypotheses)
        for text in claim_texts:
            if any(claim in text for claim in unsupported_definitive_claims) and not any(
                marker in text for marker in abstention_markers
            ):
                raise ModelRetry(
                    f"表述“{text}”把风险信号写成了当前数据不能证明的最终事实；"
                    "请保留风险判断，但改为可能性表述，并把最终结果标为无法判断。"
                )
        _trace(ctx.deps, "REPORT_VALIDATED", "报告校验通过", "证据引用和结论状态已核验。")
        return report

    return agent


def _evidence_completeness(
    dependencies: InvestigationDependencies,
) -> Literal["LOW", "MEDIUM", "HIGH"]:
    keys = _evidence_query_keys(dependencies)
    required = _required_evidence(dependencies)
    coverage = len(keys & required) / len(required)
    if coverage == 1:
        return "HIGH"
    if coverage >= 2 / 3:
        return "MEDIUM"
    return "LOW"


def _normalize_investigation_report(
    report: InvestigationReport,
    dependencies: InvestigationDependencies,
) -> InvestigationReport:
    return report.model_copy(
        update={
            "trace": list(dependencies.trace),
            "evidence_completeness": _evidence_completeness(dependencies),
            "requires_human_review": True,
        }
    )


def _partial_investigation_report(
    dependencies: InvestigationDependencies,
) -> InvestigationReport:
    missing_evidence = _missing_requirements(dependencies)
    missing_evidence.append("模型未能生成通过证据校验的完整报告")
    facts = [
        InvestigationFact(statement=item.summary[:500], evidence_ids=[item.evidence_id])
        for item in dependencies.evidence[:12]
    ]
    risk_assessment = _fallback_risk_assessment(dependencies, missing_evidence)
    _trace(
        dependencies,
        "PARTIAL_REPORT",
        "保留部分调查结果",
        "调查未完整完成；系统保留可验证事实，并将具体原因标记为无法判断。",
    )
    return InvestigationReport(
        investigation_summary=(
            "调查未完整完成。系统已保留规则识别出的风险信号和已取得的工具事实；"
            "具体形成原因及最终损失结果目前无法判断。"
        ),
        risk_assessment=risk_assessment,
        hypotheses=[
            InvestigationHypothesis(
                hypothesis_id="H-UNRESOLVED",
                statement="现有证据能否完整解释本案原因，目前无法判断。",
                status="UNRESOLVED",
                missing_evidence=missing_evidence,
            )
        ],
        facts=facts,
        limitations=["调查运行中断或最终报告未通过证据校验，未补写任何推测性结论。"],
        recommended_priority=dependencies.case.priority,
        recommended_actions=["按监测项跟踪后续变化；补齐缺失证据后重新调查具体原因。"],
        evidence_completeness=_evidence_completeness(dependencies),
        requires_human_review=True,
        trace=list(dependencies.trace),
    )


def _fallback_risk_assessment(
    dependencies: InvestigationDependencies,
    missing_requirements: Sequence[str],
) -> RiskSignalAssessment:
    """模型报告失败时，基于规则和已取得证据保留最小风险判断。"""

    evidence_ids = [item.evidence_id for item in dependencies.evidence[:9]]
    drivers = [item.summary[:300] for item in dependencies.evidence[:3]]
    if missing_requirements:
        return RiskSignalAssessment(
            stage="LIMITED",
            statement="已取得部分可验证事实，但最低证据覆盖尚未完成，当前只能确认存在待核风险信号。",
            evidence_ids=evidence_ids,
            drivers=drivers,
            watch_items=["补齐缺失证据后，重新判断风险趋势和具体原因。"],
        )

    signal_codes = {signal.signal_code for signal in dependencies.case.signals}
    if signal_codes & {"AR_OPERATING_EXPOSURE_BUILDUP", "INV_BUILDUP_SALES_SLOWDOWN"}:
        stage: Literal["EARLY_WARNING", "DETERIORATING", "LIMITED"] = "EARLY_WARNING"
        statement = "现有证据支持经营指标出现方向一致的早期风险信号，具体成因仍需补证。"
    elif signal_codes & {"AR_OPERATING_DEEP_OVERDUE", "INV_STALE_NO_SALES"}:
        stage = "DETERIORATING"
        statement = "现有证据支持风险敞口已经恶化，具体成因和最终损失仍需补证。"
    else:
        stage = "EARLY_WARNING"
        statement = "现有证据支持存在需要持续观察的早期风险信号，具体成因仍需补证。"
    if dependencies.case.case_type == "PRE_TRANSACTION":
        watch_items = [
            "拟交易金额和账期是否需要附加预付款、分段交付或增信条件。",
            "新增交易后客户应收敞口和回款节奏是否仍可接受。",
        ]
    else:
        watch_items = (
            ["后续回款能否覆盖新增销售和到期应收。", "超期金额和账龄是否继续扩大。"]
            if dependencies.case.case_type == "ACCOUNTS_RECEIVABLE"
            else ["库存金额和高库龄占比是否继续增加。", "后续销售速度能否消化现有库存。"]
        )
    return RiskSignalAssessment(
        stage=stage,
        statement=statement,
        evidence_ids=evidence_ids,
        drivers=drivers,
        watch_items=watch_items,
    )


def _investigation_prompt(case: InvestigationCaseInput) -> str:
    return case.model_dump_json()


async def stream_investigation_agent(
    settings: Settings,
    case: InvestigationCaseInput,
    *,
    model: Model | None = None,
) -> AsyncIterator[InvestigationAgentProgress | InvestigationOutcome]:
    """运行调查并把动态查询和证据事件提炼成可观察进度。"""

    agent = _create_investigation_agent(settings, model)
    dependencies = InvestigationDependencies(store=DuckDBStore(settings.database_path), case=case)
    emitted_evidence_ids: set[str] = set()
    validation_announced = False
    try:
        async with agent.run_stream_events(
            _investigation_prompt(case),
            deps=dependencies,
            usage_limits=UsageLimits(
                request_limit=12,
                tool_calls_limit=10,
                output_tokens_limit=40_000,
            ),
        ) as events:
            async for event in events:
                if isinstance(event, FunctionToolCallEvent):
                    raw_tool_name = event.part.tool_name
                    if raw_tool_name in allowed_investigation_tools(case.case_type):
                        tool_name = raw_tool_name
                        yield InvestigationAgentProgress(
                            event_type="TOOL_STARTED",
                            message=f"正在执行 {tool_name}，获取受控只读业务信息。",
                            tool_name=tool_name,
                        )
                elif isinstance(event, FunctionToolResultEvent) and isinstance(
                    event.part, ToolReturnPart
                ):
                    raw_tool_name = event.part.tool_name
                    if raw_tool_name == "discover_evidence_capabilities":
                        yield InvestigationAgentProgress(
                            event_type="TOOL_COMPLETED",
                            message=(
                                "当前数据快照的证据能力已发现，可以选择数据集、粒度、指标和窗口。"
                            ),
                            tool_name="discover_evidence_capabilities",
                        )
                    elif raw_tool_name == "search_business_records":
                        yield InvestigationAgentProgress(
                            event_type="TOOL_COMPLETED",
                            message="案件范围内的业务标识搜索已经完成。",
                            tool_name="search_business_records",
                        )
                    elif raw_tool_name in allowed_investigation_tools(case.case_type):
                        tool_name = raw_tool_name
                        evidence = next(
                            (
                                item
                                for item in reversed(dependencies.evidence)
                                if item.tool_name == tool_name
                                and item.evidence_id not in emitted_evidence_ids
                            ),
                            None,
                        )
                        if evidence is not None:
                            emitted_evidence_ids.add(evidence.evidence_id)
                            yield InvestigationAgentProgress(
                                event_type="TOOL_COMPLETED",
                                message=evidence.summary,
                                tool_name=tool_name,
                                evidence=evidence,
                            )
                    if not validation_announced and _minimum_evidence_ready(dependencies):
                        validation_announced = True
                        yield InvestigationAgentProgress(
                            event_type="VALIDATION_STARTED",
                            message="最低证据覆盖已满足，正在校验引用、推测边界和报告完整性。",
                        )
                elif isinstance(event, AgentRunResultEvent):
                    report = _normalize_investigation_report(event.result.output, dependencies)
                    usage = event.result.usage
                    yield InvestigationOutcome(
                        report=report,
                        evidence=list(dependencies.evidence),
                        called_tools=tuple(sorted(dependencies.called_tools)),
                        usage={
                            "requests": usage.requests,
                            "tool_calls": usage.tool_calls,
                            "input_tokens": usage.input_tokens,
                            "cache_write_tokens": usage.cache_write_tokens,
                            "cache_read_tokens": usage.cache_read_tokens,
                            "output_tokens": usage.output_tokens,
                            "total_tokens": usage.total_tokens,
                            "cost": str(usage.cost) if usage.cost is not None else None,
                        },
                    )
    except Exception:
        logger.exception(
            "调查 Agent 运行中断：case_id=%s evidence_count=%d",
            case.case_id,
            len(dependencies.evidence),
        )
        if not dependencies.evidence:
            raise
        yield InvestigationAgentProgress(
            event_type="ERROR",
            message="调查未完整完成；已取得的证据将被保留，未取得的结论标记为无法判断。",
        )
        yield InvestigationOutcome(
            report=_partial_investigation_report(dependencies),
            evidence=list(dependencies.evidence),
            partial=True,
            called_tools=tuple(sorted(dependencies.called_tools)),
        )


async def run_investigation_agent(
    settings: Settings,
    case: InvestigationCaseInput,
    *,
    model: Model | None = None,
) -> InvestigationOutcome:
    """运行一次调查并返回最终结果，供服务层和离线评测复用。"""

    outcome: InvestigationOutcome | None = None
    async for event in stream_investigation_agent(settings, case, model=model):
        if isinstance(event, InvestigationOutcome):
            outcome = event
    if outcome is None:
        raise RuntimeError("调查 Agent 未产生最终报告。")
    return outcome

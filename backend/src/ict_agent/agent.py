"""DeepSeek Provider、Pydantic AI 动态调查内核与受控工具注册。"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import uuid4

import httpx
from pydantic import JsonValue
from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    ModelRetry,
    PromptedOutput,
    RunContext,
    UsageLimits,
)
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelResponse,
    ToolReturnPart,
)
from pydantic_ai.models import (
    Model,
    ModelRequestParameters,
    StreamedResponse,
    create_async_http_client,
)
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.settings import ModelSettings
from pydantic_core import to_jsonable_python

from ict_agent import tools as analysis_tools
from ict_agent.config import OFFICIAL_DEEPSEEK_BASE_URL, Settings
from ict_agent.data import DuckDBStore
from ict_agent.models import (
    BusinessDataCatalog,
    BusinessRecordSearchQuery,
    Evidence,
    EvidenceQuery,
    InvestigationCaseInput,
    InvestigationFact,
    InvestigationHypothesis,
    InvestigationProfile,
    InvestigationProtocolSnapshot,
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
from ict_agent.prompts import INVESTIGATION_INSTRUCTIONS, INVESTIGATION_OUTPUT_TEMPLATE
from ict_agent.semantic import time_window_covers

logger = logging.getLogger(__name__)

INVESTIGATION_TOOLS: tuple[InvestigationToolName, ...] = (
    "inspect_data",
    "find_records",
    "get_evidence",
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


def allowed_investigation_tools(
    investigation_profile: InvestigationProfile,
) -> tuple[InvestigationToolName, ...]:
    """应收与库存共用同一组受治理调查工具。"""

    del investigation_profile
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
    protocol: InvestigationProtocolSnapshot | None = None


@dataclass(frozen=True)
class InvestigationAgentProgress:
    """从 Pydantic AI 事件中提炼的前端可观察进度。"""

    event_type: InvestigationStreamEventType
    message: str
    tool_name: InvestigationToolName | None = None
    evidence: Evidence | None = None


def _serialize_messages(messages: Sequence[ModelMessage]) -> list[dict[str, JsonValue]]:
    """按 Pydantic AI 原始 JSON 结构序列化模型消息。"""

    return cast(
        list[dict[str, JsonValue]],
        ModelMessagesTypeAdapter.dump_python(list(messages), mode="json"),
    )


SENSITIVE_HTTP_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-api-key",
}


def _capture_headers(headers: httpx.Headers) -> dict[str, str]:
    """保留真实 HTTP 头字段，同时遮蔽凭据和会话信息。"""

    return {
        name: "[REDACTED]" if name.lower() in SENSITIVE_HTTP_HEADERS else value
        for name, value in headers.multi_items()
    }


def _is_chat_completions_url(url: httpx.URL) -> bool:
    return url.path.rstrip("/").endswith("/chat/completions")


@dataclass
class DeepSeekWireCapture:
    """抓取最后一次 DeepSeek Chat Completions HTTP 事务。"""

    request: dict[str, JsonValue] | None = None
    response: dict[str, JsonValue] | None = None

    async def capture_request(self, request: httpx.Request) -> None:
        if not _is_chat_completions_url(request.url):
            return
        body = await request.aread()
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise ValueError("DeepSeek Chat Completions 请求体必须是 JSON 对象。")
        self.request = cast(
            dict[str, JsonValue],
            to_jsonable_python(
                {
                    "method": request.method,
                    "url": str(request.url),
                    "headers": _capture_headers(request.headers),
                    "body": parsed,
                }
            ),
        )
        self.response = None

    async def capture_response(self, response: httpx.Response) -> None:
        if not _is_chat_completions_url(response.request.url):
            return
        self.response = cast(
            dict[str, JsonValue],
            to_jsonable_python(
                {
                    "status_code": response.status_code,
                    "headers": _capture_headers(response.headers),
                    "body": None,
                }
            ),
        )
        response.stream = _CapturedResponseStream(
            cast(httpx.AsyncByteStream, response.stream), self
        )

    def record_response_body(self, body: bytes) -> None:
        """保存完整 JSON 响应，或把 Chat Completions SSE 解析为事件列表。"""

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            self._set_response_body(cast(JsonValue, to_jsonable_python(parsed)))
            return

        events: list[dict[str, JsonValue]] = []
        normalized = body.replace(b"\r\n", b"\n")
        for block in normalized.split(b"\n\n"):
            event_name = next(
                (
                    line[6:].lstrip().decode("utf-8", errors="replace")
                    for line in block.splitlines()
                    if line.startswith(b"event:")
                ),
                "message",
            )
            data_lines = [
                line[5:].lstrip() for line in block.splitlines() if line.startswith(b"data:")
            ]
            if not data_lines:
                continue
            event_payload = b"\n".join(data_lines)
            if event_payload == b"[DONE]":
                events.append({"event": event_name, "data": "[DONE]"})
                continue
            try:
                event_data = json.loads(event_payload)
            except json.JSONDecodeError:
                continue
            events.append(
                cast(
                    dict[str, JsonValue],
                    to_jsonable_python({"event": event_name, "data": event_data}),
                )
            )
        self._set_response_body(
            cast(JsonValue, {"format": "sse", "events": events})
            if events
            else body.decode("utf-8", errors="replace")
        )

    def _set_response_body(self, body: JsonValue) -> None:
        if self.response is None:
            self.response = {"status_code": 0, "headers": {}, "body": body}
        else:
            self.response["body"] = body


class _CapturedResponseStream(httpx.AsyncByteStream):
    """旁路复制流式响应，不延迟或消费 Pydantic AI 正在读取的 SSE。"""

    def __init__(self, wrapped: httpx.AsyncByteStream, capture: DeepSeekWireCapture) -> None:
        self._wrapped = wrapped
        self._capture = capture
        self._chunks: list[bytes] = []

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._wrapped:
                self._chunks.append(chunk)
                yield chunk
        finally:
            self._capture.record_response_body(b"".join(self._chunks))

    async def aclose(self) -> None:
        await self._wrapped.aclose()


def _create_capture_http_client(capture: DeepSeekWireCapture) -> httpx.AsyncClient:
    client = create_async_http_client()
    client.event_hooks["request"].append(capture.capture_request)
    client.event_hooks["response"].append(capture.capture_response)
    return client


class _CapturedDeepSeekProvider(DeepSeekProvider):
    """使用 DeepSeek 官方 OpenAI 格式端点，并管理带抓取钩子的客户端生命周期。"""

    def __init__(self, api_key: str, capture: DeepSeekWireCapture) -> None:
        self._capture = capture
        http_client = _create_capture_http_client(capture)
        super().__init__(api_key=api_key, http_client=http_client)
        self._own_http_client = http_client
        self._http_client_factory = self._new_http_client

    def _new_http_client(self) -> httpx.AsyncClient:
        return _create_capture_http_client(self._capture)


class InvestigationProtocolRecorder(WrapperModel):
    """保留最后一次完整 DeepSeek Chat Completions HTTP 事务。"""

    def __init__(self, wrapped: Model, capture: DeepSeekWireCapture | None = None) -> None:
        super().__init__(wrapped)
        self._capture = capture
        self._request_index = 0
        self._model_settings: dict[str, JsonValue] = {}
        self._request_parameters: dict[str, JsonValue] = {}
        self._messages: list[ModelMessage] = []

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        self._request_index += 1
        self._model_settings = cast(dict[str, JsonValue], to_jsonable_python(model_settings or {}))
        self._request_parameters = cast(
            dict[str, JsonValue], to_jsonable_python(model_request_parameters)
        )
        self._messages = list(messages)
        async with self.wrapped.request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as response_stream:
            yield response_stream

    def snapshot(
        self, response: ModelResponse | None = None
    ) -> InvestigationProtocolSnapshot | None:
        """输出最后请求及其响应；尚未请求模型时不伪造记录。"""

        if self._request_index == 0:
            return None

        if self._capture is not None and self._capture.request is not None:
            capture_source: Literal["wire", "pydantic_ai_test"] = "wire"
            request = self._capture.request
            serialized_response = self._capture.response
        else:
            capture_source = "pydantic_ai_test"
            request = {
                "method": "POST",
                "url": f"pydantic-ai://{self.system}/chat/completions",
                "headers": {},
                "body": {
                    "model": self.model_name,
                    "messages": cast(JsonValue, _serialize_messages(self._messages)),
                    "stream": True,
                    "model_settings": self._model_settings,
                    "model_request_parameters": self._request_parameters,
                },
            }
            serialized_response = (
                {"pydantic_ai_model_response": _serialize_messages([response])[0]}
                if response is not None
                else None
            )
        return InvestigationProtocolSnapshot(
            request_index=self._request_index,
            capture_source=capture_source,
            request=request,
            response=serialized_response,
        )


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
        if item.tool_name == "get_evidence"
    }


def _required_evidence(dependencies: InvestigationDependencies) -> set[tuple[str, str]]:
    if dependencies.case.investigation_profile == "INVENTORY":
        return set(INVENTORY_CORE_EVIDENCE)
    if dependencies.case.investigation_profile == "PRE_TRANSACTION":
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
    """把统一案件存储模型映射为冻结的 V4 调查输入契约。"""

    return InvestigationCaseInput(
        case_id=case.case_id,
        source=case.source,
        investigation_profile=case.investigation_profile,
        subject_type=case.subject_type,
        subject_id=case.subject_id,
        subject_label=case.subject_label,
        business_type=case.business_type,
        subject_context=case.subject_context,
        observation_date=case.observation_date,
        priority=case.priority,
        exposure_amount=case.exposure_amount,
        summary=case.summary,
        source_set_version=case.source_set_version,
        source_snapshot_id=case.source_snapshot_id,
        signals=case.signals,
        data_quality=case.data_quality,
    )


def _create_model(
    settings: Settings,
    model: Model | None,
    capture: DeepSeekWireCapture | None = None,
) -> Model:
    if model is not None:
        return model
    if settings.deepseek_base_url != OFFICIAL_DEEPSEEK_BASE_URL:
        raise ValueError("当前 Agent 只允许使用 DeepSeek 官方 Provider。")
    if settings.deepseek_api_key is None:
        raise ValueError("缺少 DeepSeek API Key。")
    provider = _CapturedDeepSeekProvider(
        api_key=settings.deepseek_api_key.get_secret_value(),
        capture=capture or DeepSeekWireCapture(),
    )
    return OpenAIChatModel(
        settings.deepseek_model,
        provider=provider,
        profile=OpenAIModelProfile(openai_chat_supports_max_completion_tokens=False),
    )


def _create_investigation_agent(
    settings: Settings,
    model: Model | None = None,
) -> Agent[InvestigationDependencies, InvestigationReport]:
    """创建只暴露检查、搜索和取证三项受治理能力的调查 Agent。"""

    agent = Agent[InvestigationDependencies, InvestigationReport](
        _create_model(settings, model),
        output_type=PromptedOutput(
            InvestigationReport,
            name="investigation_report",
            description="Return the final evidence-grounded investigation report as JSON.",
            template=INVESTIGATION_OUTPUT_TEMPLATE,
        ),
        deps_type=InvestigationDependencies,
        instructions=INVESTIGATION_INSTRUCTIONS,
        model_settings=OpenAIChatModelSettings(
            max_tokens=16_000,
            openai_reasoning_effort="high",
            extra_body={"thinking": {"type": "enabled"}},
        ),
        retries=3,
    )

    @agent.tool(sequential=True)
    def inspect_data(
        ctx: RunContext[InvestigationDependencies],
    ) -> BusinessDataCatalog:
        """List the datasets, query options, and limits available for the current case."""

        if ctx.deps.catalog_discovered:
            raise ModelRetry("Data has already been inspected. Request the next required evidence.")
        catalog = analysis_tools.discover_evidence_capabilities(
            ctx.deps.store,
            ctx.deps.case.investigation_profile,
            ctx.deps.case.subject_context,
            ctx.deps.case.observation_date,
            business_type=ctx.deps.case.business_type,
        )
        ctx.deps.catalog_discovered = True
        ctx.deps.called_tools.add("inspect_data")
        return catalog

    @agent.tool(sequential=True)
    def find_records(
        ctx: RunContext[InvestigationDependencies], search: BusinessRecordSearchQuery
    ) -> ToolResult:
        """Find related customer, contract, order, or material identifiers in this case."""

        if not ctx.deps.catalog_discovered:
            raise ModelRetry("Call inspect_data before searching for record identifiers.")
        signature = search.model_dump_json()
        if signature in ctx.deps.search_signatures:
            raise ModelRetry("This record search is a duplicate. Reuse it or change the query.")
        result = analysis_tools.search_business_records(
            ctx.deps.store,
            ctx.deps.case.investigation_profile,
            ctx.deps.case.subject_context,
            search,
        )
        ctx.deps.search_signatures.add(signature)
        ctx.deps.called_tools.add("find_records")
        return result

    @agent.tool(sequential=True)
    def get_evidence(
        ctx: RunContext[InvestigationDependencies], query: EvidenceQuery
    ) -> ToolResult:
        """Get case-scoped evidence using query options returned by inspect_data."""

        if not ctx.deps.catalog_discovered:
            raise ModelRetry("Call inspect_data before requesting evidence.")
        signature = query.model_dump_json()
        if signature in ctx.deps.query_signatures or _query_is_redundant(
            ctx.deps.query_history, query
        ):
            raise ModelRetry("Existing evidence already covers this query. Reuse its evidence_id.")
        try:
            result = analysis_tools.query_business_evidence(
                ctx.deps.store,
                ctx.deps.case.investigation_profile,
                ctx.deps.case.subject_context,
                query,
                business_type=ctx.deps.case.business_type,
            )
        except analysis_tools.AnalysisInputError as exc:
            raise ModelRetry(
                f"Unsupported governed query: {exc} Adjust it to the inspect_data catalog."
            ) from exc
        ctx.deps.query_signatures.add(signature)
        ctx.deps.query_history.append(query)
        return _record_investigation_evidence(
            ctx.deps,
            "get_evidence",
            result,
            arguments=query.model_dump(mode="json"),
        )

    @agent.output_validator
    def validate_investigation_report(
        ctx: RunContext[InvestigationDependencies], report: InvestigationReport
    ) -> InvestigationReport:
        """Reject incomplete evidence, invalid citations, and unsupported conclusions."""

        missing = _missing_requirements(ctx.deps)
        if missing:
            raise ModelRetry(
                f"Minimum evidence coverage is incomplete: {missing}. Continue gathering evidence."
            )
        valid_ids = {item.evidence_id for item in ctx.deps.evidence}
        invalid_risk_ids = set(report.risk_assessment.evidence_ids) - valid_ids
        if invalid_risk_ids:
            raise ModelRetry(
                "The risk assessment cites unknown evidence IDs: "
                f"{sorted(invalid_risk_ids)}. Use only IDs returned in this run."
            )
        if not report.facts:
            raise ModelRetry(
                "The report must include at least one fact established by a tool result."
            )
        if ctx.deps.case.data_quality.status in ("WARNING", "UNKNOWN") and not report.limitations:
            raise ModelRetry(
                "Data quality is not PASS. Preserve the data limitation in `limitations`."
            )
        for fact in report.facts:
            if not fact.evidence_ids:
                raise ModelRetry(f"Fact {fact.statement!r} does not cite an evidence_id.")
            invalid_ids = set(fact.evidence_ids) - valid_ids
            if invalid_ids:
                raise ModelRetry(f"A fact cites unknown evidence IDs: {sorted(invalid_ids)}.")
        for hypothesis in report.hypotheses:
            refs = set(hypothesis.supporting_evidence_ids) | set(
                hypothesis.contradicting_evidence_ids
            )
            overlap = set(hypothesis.supporting_evidence_ids) & set(
                hypothesis.contradicting_evidence_ids
            )
            if overlap:
                raise ModelRetry(
                    f"Hypothesis {hypothesis.statement!r} uses the same evidence as both "
                    "supporting "
                    f"and contradicting: {sorted(overlap)}. Keep each ID on the correct side only."
                )
            invalid_ids = refs - valid_ids
            if invalid_ids:
                raise ModelRetry(f"A hypothesis cites unknown evidence IDs: {sorted(invalid_ids)}.")
            if hypothesis.status == "SUPPORTED" and not hypothesis.supporting_evidence_ids:
                raise ModelRetry(
                    f"SUPPORTED hypothesis {hypothesis.statement!r} has no supporting evidence."
                )
            if hypothesis.status == "WEAKENED" and not hypothesis.contradicting_evidence_ids:
                raise ModelRetry(
                    f"WEAKENED hypothesis {hypothesis.statement!r} has no contradicting evidence."
                )
            if (
                hypothesis.status == "UNRESOLVED"
                and not hypothesis.missing_evidence
                and not (
                    hypothesis.supporting_evidence_ids and hypothesis.contradicting_evidence_ids
                )
            ):
                raise ModelRetry(
                    f"UNRESOLVED hypothesis {hypothesis.statement!r} must identify missing "
                    "evidence "
                    "or cite a genuine evidence conflict."
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
                    f"Statement {text!r} turns a risk signal into a final fact that current data "
                    "cannot prove. Preserve the signal, use possibility language, and mark the "
                    "final "
                    "outcome as unresolved."
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
    if dependencies.case.investigation_profile == "PRE_TRANSACTION":
        watch_items = [
            "拟交易金额和账期是否需要附加预付款、分段交付或增信条件。",
            "新增交易后客户应收敞口和回款节奏是否仍可接受。",
        ]
    else:
        watch_items = (
            ["后续回款能否覆盖新增销售和到期应收。", "超期金额和账龄是否继续扩大。"]
            if dependencies.case.investigation_profile == "RECEIVABLES"
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

    capture = DeepSeekWireCapture() if model is None else None
    recorder = InvestigationProtocolRecorder(
        _create_model(settings, model, capture), capture=capture
    )
    agent = _create_investigation_agent(settings, recorder)
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
                    if raw_tool_name in allowed_investigation_tools(case.investigation_profile):
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
                    if raw_tool_name == "inspect_data":
                        yield InvestigationAgentProgress(
                            event_type="TOOL_COMPLETED",
                            message=(
                                "当前数据快照的证据能力已发现，可以选择数据集、粒度、指标和窗口。"
                            ),
                            tool_name="inspect_data",
                        )
                    elif raw_tool_name == "find_records":
                        yield InvestigationAgentProgress(
                            event_type="TOOL_COMPLETED",
                            message="案件范围内的业务标识搜索已经完成。",
                            tool_name="find_records",
                        )
                    elif raw_tool_name in allowed_investigation_tools(case.investigation_profile):
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
                    messages = event.result.all_messages()
                    final_response = next(
                        (
                            message
                            for message in reversed(messages)
                            if isinstance(message, ModelResponse)
                        ),
                        None,
                    )
                    yield InvestigationOutcome(
                        report=report,
                        evidence=list(dependencies.evidence),
                        called_tools=tuple(sorted(dependencies.called_tools)),
                        protocol=recorder.snapshot(final_response),
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
            protocol=recorder.snapshot(),
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

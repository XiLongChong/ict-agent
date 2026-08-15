"""V2 数据发现、受控查询和证据校验测试，不访问真实模型。"""

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from ict_agent.agent import (
    DeepSeekWireCapture,
    InvestigationOutcome,
    _create_model,
    _query_is_redundant,
    _serialize_messages,
    build_investigation_case_input,
    run_investigation_agent,
    stream_investigation_agent,
)
from ict_agent.config import Settings
from ict_agent.models import (
    EvidenceQuery,
    InvestigationDataQuality,
    InvestigationReport,
    InvestigationSignalInput,
    RiskCaseDetail,
    ToolResult,
)
from ict_agent.prompts import INVESTIGATION_INSTRUCTIONS, INVESTIGATION_OUTPUT_TEMPLATE
from pydantic import BaseModel
from pydantic_ai import Agent, PromptedOutput
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.deepseek import DeepSeekProvider

pytestmark = pytest.mark.anyio

QUERIES = [
    {
        "dataset": "receivables",
        "grain": "month",
        "metrics": ["ar_amount", "overdue_amount", "overdue_60_amount", "overdue_rate"],
        "time_window": "last_12_months",
        "sort_by": None,
        "sort_direction": "desc",
        "limit": 12,
    },
    {
        "dataset": "sales_payments",
        "grain": "month",
        "metrics": ["sales_amount", "payment_amount", "gross_profit"],
        "time_window": "last_6_months",
        "sort_by": None,
        "sort_direction": "desc",
        "limit": 6,
    },
    {
        "dataset": "receivables",
        "grain": "order",
        "metrics": ["ar_amount", "overdue_amount", "overdue_60_amount", "max_overdue_days"],
        "time_window": "latest",
        "sort_by": "overdue_amount",
        "sort_direction": "desc",
        "limit": 20,
    },
    {
        "dataset": "credit",
        "grain": "customer",
        "metrics": ["credit_limit", "list_status", "credit_insurance"],
        "time_window": "latest",
        "sort_by": None,
        "sort_direction": "desc",
        "limit": 1,
    },
]

PRE_TRANSACTION_QUERIES = [
    {
        "dataset": "proposal",
        "grain": "order",
        "metrics": ["proposed_amount", "proposed_term_days", "expected_margin_rate"],
        "time_window": "latest",
        "limit": 1,
    },
    {
        "dataset": "customer_profile",
        "grain": "business_type",
        "metrics": [
            "historical_order_count",
            "median_order_amount",
            "p90_order_amount",
            "median_payment_days",
            "median_margin_rate",
        ],
        "time_window": "all",
        "limit": 1,
    },
    {
        "dataset": "receivables",
        "grain": "month",
        "metrics": ["ar_amount", "overdue_amount"],
        "time_window": "last_12_months",
        "limit": 12,
    },
    {
        "dataset": "sales_payments",
        "grain": "month",
        "metrics": ["sales_amount", "payment_amount"],
        "time_window": "last_6_months",
        "limit": 6,
    },
    {
        "dataset": "credit",
        "grain": "customer",
        "metrics": ["credit_limit", "list_status"],
        "time_window": "latest",
        "limit": 1,
    },
]


def _case() -> RiskCaseDetail:
    return RiskCaseDetail(
        case_id="case-test",
        source="RULE_SCAN",
        investigation_profile="RECEIVABLES",
        subject_type="CUSTOMER",
        subject_id="C015",
        subject_label="C015 测试客户",
        subject_context={"customer_id": "C015", "customer_name": "测试客户"},
        observation_date="2026-07-31",
        status="PENDING_AGENT_REVIEW",
        priority="HIGH",
        exposure_amount=1000,
        summary="测试应收案件",
        signal_overview="应收超期预警",
        signal_count=1,
        source_set_version="test",
        source_snapshot_id="snapshot-test",
        data_quality=InvestigationDataQuality(status="PASS"),
        updated_at="2026-08-08T00:00:00",
        signals=[
            InvestigationSignalInput(
                signal_id="hit-test",
                signal_code="AR_TEST",
                signal_name="测试规则",
                source_version="test",
                severity="HIGH",
                exposure_amount=600,
                reason="存在深度超期",
                metrics={"overdue_amount": 600},
                threshold_source="测试",
                threshold_version="test",
                sources=["ar_snapshots"],
                period="2026-07-31",
            )
        ],
    )


def _pre_transaction_case() -> RiskCaseDetail:
    return RiskCaseDetail(
        case_id="pre-case-test",
        source="PRE_TRANSACTION_SIMULATION",
        investigation_profile="PRE_TRANSACTION",
        subject_type="CUSTOMER",
        subject_id="C015",
        subject_label="C015 测试客户",
        business_type="DISTRIBUTION",
        subject_context={
            "simulation_id": "sim-test",
            "customer_id": "C015",
            "customer_name": "测试客户",
            "amount_yuan": 180,
            "proposed_term_days": 45,
            "expected_margin_rate": 0.2,
            "scenario": "ANOMALY",
            "generated_at": "2026-08-14T00:00:00+00:00",
            "simulated": True,
        },
        observation_date="2026-08-14",
        status="PENDING_AGENT_REVIEW",
        priority="HIGH",
        exposure_amount=180,
        summary="模拟交易需要成交前调查。",
        signal_overview="新交易事前调查",
        signal_count=1,
        source_set_version="pre-transaction-simulator-1.0",
        source_snapshot_id="snapshot-test",
        data_quality=InvestigationDataQuality(
            status="WARNING",
            warnings=["历史正订单少于 5 笔。"],
        ),
        updated_at="2026-08-14T00:00:00+00:00",
        signals=[
            InvestigationSignalInput(
                signal_id="signal-pre-test",
                signal_code="PRE_TRANSACTION_REVIEW",
                signal_name="新交易事前调查",
                source_version="pre-transaction-simulator-1.0",
                severity="HIGH",
                exposure_amount=180,
                reason="拟交易金额高于历史基线。",
                metrics={"proposed_amount_yuan": 180},
                threshold_source="客户同业务历史分布",
                threshold_version="snapshot-test",
                sources=["sales", "payments", "customer_credit"],
                period="2026-08-14",
            )
        ],
    )


def _returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [
        part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)
    ]


def _input():
    return build_investigation_case_input(_case())


def _pre_input():
    return build_investigation_case_input(_pre_transaction_case())


def _investigation_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    returns = _returns(messages)
    returned_names = [part.tool_name for part in returns]
    if "inspect_data" not in returned_names:
        return ModelResponse(parts=[ToolCallPart("inspect_data", {})])
    query_count = returned_names.count("get_evidence")
    if query_count < len(QUERIES):
        return ModelResponse(parts=[ToolCallPart("get_evidence", {"query": QUERIES[query_count]})])
    evidence_ids = [
        part.content.evidence_id
        for part in returns
        if isinstance(part.content, ToolResult) and part.content.evidence_id
    ]
    output = {
        "investigation_summary": "证据确认存在超期事实，并支持进一步核查项目回款背景。",
        "risk_assessment": {
            "stage": "DETERIORATING",
            "statement": "应收和订单证据支持风险敞口需要持续复核。",
            "evidence_ids": evidence_ids[:2],
            "drivers": ["应收历史存在持续未结清记录。"],
            "counter_signals": ["部分应收尚未到期。"],
            "watch_items": ["后续回款能否覆盖到期应收。"],
        },
        "hypotheses": [
            {
                "hypothesis_id": "H1",
                "statement": "现有数据支持应收由项目合同和既有逾期共同构成。",
                "status": "SUPPORTED",
                "supporting_evidence_ids": [evidence_ids[2], evidence_ids[3]],
                "contradicting_evidence_ids": [],
                "missing_evidence": [],
            }
        ],
        "facts": [{"statement": "已取得可复核的应收月度趋势。", "evidence_ids": [evidence_ids[0]]}],
        "limitations": ["数据不包含项目验收和最终回收结果。"],
        "recommended_priority": "HIGH",
        "recommended_actions": ["人工复核项目到期计划和后续回款。"],
        "requires_human_review": True,
    }
    assert info.allow_text_output is True
    assert info.output_tools == []
    return ModelResponse(parts=[TextPart(json.dumps(output, ensure_ascii=False))])


def _recovering_query_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    returns = _returns(messages)
    returned_names = [part.tool_name for part in returns]
    has_retry = any(
        isinstance(part, RetryPromptPart) for message in messages for part in message.parts
    )
    if "inspect_data" in returned_names and "get_evidence" not in returned_names and not has_retry:
        invalid_query = {**QUERIES[0], "metrics": ["credit_limit"]}
        return ModelResponse(parts=[ToolCallPart("get_evidence", {"query": invalid_query})])
    return _investigation_model(messages, info)


def _pre_transaction_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    returns = _returns(messages)
    returned_names = [part.tool_name for part in returns]
    if "inspect_data" not in returned_names:
        return ModelResponse(parts=[ToolCallPart("inspect_data", {})])
    query_count = returned_names.count("get_evidence")
    if query_count < len(PRE_TRANSACTION_QUERIES):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "get_evidence",
                    {"query": PRE_TRANSACTION_QUERIES[query_count]},
                )
            ]
        )
    evidence_ids = [
        part.content.evidence_id
        for part in returns
        if isinstance(part.content, ToolResult) and part.content.evidence_id
    ]
    output = {
        "investigation_summary": "拟交易显著高于同业务历史基线，需要人工核对成交条件。",
        "risk_assessment": {
            "stage": "EARLY_WARNING",
            "statement": "拟交易与历史分布存在偏离，但不能据此判断客户将违约。",
            "evidence_ids": evidence_ids[:3],
            "drivers": ["拟金额高于同业务历史订单基线。"],
            "counter_signals": ["当前数据仍显示存在正常回款记录。"],
            "watch_items": ["成交前核对拟账期与当前授信敞口。"],
        },
        "hypotheses": [
            {
                "hypothesis_id": "H1",
                "statement": "拟交易金额偏离可能增加短期敞口。",
                "status": "SUPPORTED",
                "supporting_evidence_ids": evidence_ids[:3],
                "missing_evidence": [],
            }
        ],
        "facts": [
            {
                "statement": "已取得拟交易及同业务历史订单基线。",
                "evidence_ids": evidence_ids[:2],
            }
        ],
        "limitations": ["历史正订单少于 5 笔，分布稳定性有限。"],
        "recommended_priority": "HIGH",
        "recommended_actions": ["由业务人员核对账期、额度和本次交易背景。"],
        "requires_human_review": True,
    }
    assert info.allow_text_output is True
    assert info.output_tools == []
    return ModelResponse(parts=[TextPart(json.dumps(output, ensure_ascii=False))])


async def _stream_model(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    response = _investigation_model(messages, info)
    for index, part in enumerate(response.parts):
        if isinstance(part, ToolCallPart):
            yield {
                index: DeltaToolCall(
                    name=part.tool_name,
                    json_args=json.dumps(part.args),
                    tool_call_id=part.tool_call_id,
                )
            }
        elif isinstance(part, TextPart):
            yield part.content


async def _recovering_query_stream_model(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    response = _recovering_query_model(messages, info)
    for index, part in enumerate(response.parts):
        if isinstance(part, ToolCallPart):
            yield {
                index: DeltaToolCall(
                    name=part.tool_name,
                    json_args=json.dumps(part.args),
                    tool_call_id=part.tool_call_id,
                )
            }
        elif isinstance(part, TextPart):
            yield part.content


async def _pre_transaction_stream_model(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    response = _pre_transaction_model(messages, info)
    for index, part in enumerate(response.parts):
        if isinstance(part, ToolCallPart):
            yield {
                index: DeltaToolCall(
                    name=part.tool_name,
                    json_args=json.dumps(part.args),
                    tool_call_id=part.tool_call_id,
                )
            }
        elif isinstance(part, TextPart):
            yield part.content


async def _interrupted_stream_model(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    returns = _returns(messages)
    returned_names = [part.tool_name for part in returns]
    if "inspect_data" not in returned_names:
        part = ToolCallPart("inspect_data", {})
    elif "get_evidence" not in returned_names:
        part = ToolCallPart("get_evidence", {"query": QUERIES[0]})
    else:
        raise RuntimeError("simulated model interruption")
    yield {
        0: DeltaToolCall(
            name=part.tool_name,
            json_args=json.dumps(part.args),
            tool_call_id=part.tool_call_id,
        )
    }


def test_rule_case_maps_to_frozen_investigation_input() -> None:
    contract = build_investigation_case_input(_case())

    assert contract.schema_version == "4.0"
    assert contract.source == "RULE_SCAN"
    assert contract.subject_context["customer_id"] == "C015"
    assert contract.signals[0].signal_id == "hit-test"
    assert contract.signals[0].signal_code == "AR_TEST"
    assert contract.data_quality.status == "PASS"


def test_deepseek_model_profile_uses_official_chat_completions_contract(
    settings: Settings,
) -> None:
    model = _create_model(settings, None, DeepSeekWireCapture())

    assert isinstance(model, OpenAIChatModel)
    assert model.profile.get("supports_json_object_output") is True
    assert model.profile.get("openai_chat_supports_max_completion_tokens") is False
    assert model.profile.get("openai_chat_thinking_field") == "reasoning_content"


def test_model_instructions_and_output_template_are_english() -> None:
    prompt_text = (
        f"{INVESTIGATION_INSTRUCTIONS}\n{INVESTIGATION_OUTPUT_TEMPLATE}\n"
        f"{json.dumps(InvestigationReport.model_json_schema(), ensure_ascii=False)}"
    )

    assert "Call inspect_data exactly once" in prompt_text
    assert "Return the final answer as exactly one JSON object" in prompt_text
    assert not any("\u4e00" <= character <= "\u9fff" for character in prompt_text)


async def test_deepseek_prompted_output_emits_official_json_mode_parameters() -> None:
    class Answer(BaseModel):
        value: int

    captured_request: dict[str, object] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        captured_request.update(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "chat-test",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"value":1}',
                            "reasoning_content": "checked",
                        },
                        "logprobs": None,
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
                "system_fingerprint": "test",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        provider = DeepSeekProvider(api_key="test", http_client=http_client)
        model = OpenAIChatModel(
            "deepseek-v4-flash",
            provider=provider,
            profile=OpenAIModelProfile(openai_chat_supports_max_completion_tokens=False),
        )
        agent = Agent(
            model,
            output_type=PromptedOutput(Answer, template=INVESTIGATION_OUTPUT_TEMPLATE),
            instructions=INVESTIGATION_INSTRUCTIONS,
            model_settings=OpenAIChatModelSettings(
                max_tokens=16_000,
                openai_reasoning_effort="high",
                extra_body={"thinking": {"type": "enabled"}},
            ),
        )

        result = await agent.run("Return one.")

    assert result.output == Answer(value=1)
    assert captured_request["response_format"] == {"type": "json_object"}
    assert captured_request["max_tokens"] == 16_000
    assert "max_completion_tokens" not in captured_request
    assert captured_request["reasoning_effort"] == "high"
    assert captured_request["thinking"] == {"type": "enabled"}
    system_messages = [
        message for message in captured_request["messages"] if message["role"] == "system"
    ]
    system_text = json.dumps(system_messages, ensure_ascii=False)
    assert "Call inspect_data exactly once" in system_text
    assert "Return the final answer as exactly one JSON object" in system_text
    assert not any("\u4e00" <= character <= "\u9fff" for character in system_text)


def test_broader_existing_query_blocks_redundant_metric_subset() -> None:
    previous = EvidenceQuery(
        dataset="credit",
        grain="customer",
        metrics=["credit_limit", "list_status", "credit_insurance"],
        time_window="latest",
        limit=30,
    )
    subset = EvidenceQuery(
        dataset="credit",
        grain="customer",
        metrics=["credit_limit", "list_status"],
        time_window="latest",
        limit=30,
    )

    assert _query_is_redundant([previous], subset) is True


def test_broader_time_window_blocks_redundant_narrower_query() -> None:
    previous = EvidenceQuery(
        dataset="sales",
        grain="month",
        metrics=["sales_amount", "net_quantity", "return_amount", "gross_profit"],
        time_window="all",
        limit=30,
    )
    narrower = EvidenceQuery(
        dataset="sales",
        grain="month",
        metrics=["sales_amount", "net_quantity", "return_amount"],
        time_window="last_6_months",
        limit=30,
    )

    assert _query_is_redundant([previous], narrower) is True


async def test_investigation_agent_discovers_and_queries_evidence(
    settings: Settings,
) -> None:
    outcome = await run_investigation_agent(
        settings, _input(), model=FunctionModel(stream_function=_stream_model)
    )

    assert outcome.partial is False
    assert [item.tool_name for item in outcome.evidence] == ["get_evidence"] * 4
    assert [item.arguments["dataset"] for item in outcome.evidence] == [
        "receivables",
        "sales_payments",
        "receivables",
        "credit",
    ]
    assert outcome.report.evidence_completeness == "HIGH"
    assert outcome.report.risk_assessment.stage == "DETERIORATING"
    assert outcome.report.requires_human_review is True
    assert outcome.protocol is not None
    assert outcome.protocol.schema_version == "4.0"
    assert outcome.protocol.api_format == "openai_chat_completions"
    assert outcome.protocol.capture_source == "pydantic_ai_test"
    assert outcome.protocol.request_index == 6
    request_body = outcome.protocol.request["body"]
    assert request_body["model_settings"]["openai_reasoning_effort"] == "high"
    assert request_body["model_settings"]["extra_body"]["thinking"]["type"] == "enabled"
    assert request_body["model_request_parameters"]["output_mode"] == "prompted"
    function_tools = request_body["model_request_parameters"]["function_tools"]
    assert [tool["name"] for tool in function_tools] == [
        "inspect_data",
        "find_records",
        "get_evidence",
    ]
    assert [tool["description"] for tool in function_tools] == [
        "List the datasets, query options, and limits available for the current case.",
        "Find related customer, contract, order, or material identifiers in this case.",
        "Get case-scoped evidence using query options returned by inspect_data.",
    ]
    assert request_body["model_request_parameters"]["output_tools"] == []
    assert request_body["messages"][0]["instructions"]
    assert outcome.protocol.response is not None
    response_part = outcome.protocol.response["pydantic_ai_model_response"]["parts"][0]
    assert response_part["part_kind"] == "text"
    assert "final_result" not in json.dumps(outcome.protocol.model_dump(mode="json"))


async def test_deepseek_wire_capture_preserves_chat_completions_transaction() -> None:
    capture = DeepSeekWireCapture()
    request = httpx.Request(
        "POST",
        "https://api.deepseek.com/chat/completions",
        headers={"authorization": "Bearer secret-key"},
        json={
            "model": "deepseek-v4-flash",
            "messages": [
                {"role": "system", "content": "Use only tool evidence and return JSON."},
                {"role": "user", "content": "{}"},
            ],
            "max_tokens": 16_000,
            "reasoning_effort": "high",
            "thinking": {"type": "enabled"},
            "response_format": {"type": "json_object"},
            "stream": True,
        },
    )
    await capture.capture_request(request)
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream", "set-cookie": "secret-cookie"},
        request=request,
    )
    await capture.capture_response(response)
    capture.record_response_body(
        b'data: {"id":"chat_123","object":"chat.completion.chunk",'
        b'"choices":[{"index":0,"delta":{"content":"{}"}}]}\n\n'
        b"data: [DONE]\n\n"
    )

    assert capture.request is not None
    assert capture.request["method"] == "POST"
    assert capture.request["url"] == "https://api.deepseek.com/chat/completions"
    assert capture.request["headers"]["authorization"] == "[REDACTED]"
    assert capture.request["body"]["max_tokens"] == 16_000
    assert capture.request["body"]["response_format"] == {"type": "json_object"}
    assert capture.request["body"]["messages"][0]["role"] == "system"
    assert capture.response is not None
    assert capture.response["status_code"] == 200
    assert capture.response["headers"]["set-cookie"] == "[REDACTED]"
    assert capture.response["body"]["format"] == "sse"
    assert capture.response["body"]["events"][0]["event"] == "message"
    assert capture.response["body"]["events"][0]["data"]["id"] == "chat_123"
    assert capture.response["body"]["events"][1]["data"] == "[DONE]"


def test_protocol_serialization_preserves_model_thinking_content() -> None:
    messages = [
        ModelResponse(
            parts=[ThinkingPart(content="用于调查调试的模型草稿", id="reasoning_content")]
        )
    ]

    serialized = _serialize_messages(messages)

    assert serialized[0]["parts"][0]["part_kind"] == "thinking"
    assert serialized[0]["parts"][0]["content"] == "用于调查调试的模型草稿"


async def test_pre_transaction_agent_uses_history_aligned_evidence(
    settings: Settings,
) -> None:
    outcome = await run_investigation_agent(
        settings,
        _pre_input(),
        model=FunctionModel(stream_function=_pre_transaction_stream_model),
    )

    assert outcome.partial is False
    assert [item.arguments["dataset"] for item in outcome.evidence] == [
        "proposal",
        "customer_profile",
        "receivables",
        "sales_payments",
        "credit",
    ]
    assert outcome.report.risk_assessment.stage == "EARLY_WARNING"
    assert outcome.report.limitations


async def test_investigation_agent_can_recover_from_invalid_controlled_query(
    settings: Settings,
) -> None:
    outcome = await run_investigation_agent(
        settings,
        _input(),
        model=FunctionModel(stream_function=_recovering_query_stream_model),
    )

    assert outcome.partial is False
    assert len(outcome.evidence) == 4
    assert outcome.report.evidence_completeness == "HIGH"


async def test_investigation_stream_exposes_discovery_queries_and_validation(
    settings: Settings,
) -> None:
    events = [
        event
        async for event in stream_investigation_agent(
            settings, _input(), model=FunctionModel(stream_function=_stream_model)
        )
    ]

    progress_types = [
        event.event_type for event in events if not isinstance(event, InvestigationOutcome)
    ]
    assert "PLAN_PUBLISHED" not in progress_types
    assert "PLAN_UPDATED" not in progress_types
    assert progress_types.count("TOOL_STARTED") == 5
    assert progress_types.count("TOOL_COMPLETED") == 5
    assert "VALIDATION_STARTED" in progress_types


async def test_interrupted_investigation_preserves_evidence_and_abstains(
    settings: Settings,
) -> None:
    outcome = await run_investigation_agent(
        settings, _input(), model=FunctionModel(stream_function=_interrupted_stream_model)
    )

    assert outcome.partial is True
    assert len(outcome.evidence) == 1
    assert "无法判断" in outcome.report.investigation_summary
    assert outcome.report.hypotheses[0].status == "UNRESOLVED"
    assert outcome.report.facts[0].evidence_ids == [outcome.evidence[0].evidence_id]
    assert outcome.report.risk_assessment.stage == "LIMITED"

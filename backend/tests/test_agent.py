"""V2 数据发现、受控查询和证据校验测试，不访问真实模型。"""

import json
from collections.abc import AsyncIterator

import pytest
from ict_agent.agent import (
    InvestigationOutcome,
    build_investigation_case_input,
    run_investigation_agent,
    stream_investigation_agent,
)
from ict_agent.config import Settings
from ict_agent.models import RiskCaseDetail, RuleHit, ToolResult
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

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
        "dataset": "contracts",
        "grain": "contract",
        "metrics": ["contract_amount", "payment_amount", "ar_amount", "overdue_amount"],
        "time_window": "latest",
        "sort_by": "ar_amount",
        "sort_direction": "desc",
        "limit": 20,
    },
]


def _case() -> RiskCaseDetail:
    return RiskCaseDetail(
        case_id="case-test",
        case_type="ACCOUNTS_RECEIVABLE",
        entity_type="CUSTOMER",
        entity_id="C015",
        entity_label="C015 测试客户",
        entity_context={"customer_id": "C015", "customer_name": "测试客户"},
        observation_date="2026-07-31",
        status="OPEN",
        priority="HIGH",
        exposure_amount=1000,
        summary="测试应收案件",
        rule_hit_count=1,
        rule_set_version="test",
        updated_at="2026-08-08T00:00:00",
        rule_hits=[
            RuleHit(
                rule_hit_id="hit-test",
                rule_id="AR_TEST",
                rule_name="测试规则",
                rule_version="test",
                severity="HIGH",
                exposure_amount=600,
                reason="存在深度超期",
                metrics={"overdue_amount": 600},
                threshold_source="测试",
                sources=["ar_snapshots"],
                period="2026-07-31",
            )
        ],
    )


def _returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [
        part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)
    ]


def _investigation_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    returns = _returns(messages)
    returned_names = [part.tool_name for part in returns]
    if "discover_business_data" not in returned_names:
        return ModelResponse(parts=[ToolCallPart("discover_business_data", {})])
    query_count = returned_names.count("query_business_evidence")
    if query_count < len(QUERIES):
        return ModelResponse(
            parts=[ToolCallPart("query_business_evidence", {"query": QUERIES[query_count]})]
        )
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
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output)])


def _recovering_query_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    returns = _returns(messages)
    returned_names = [part.tool_name for part in returns]
    has_retry = any(
        isinstance(part, RetryPromptPart) for message in messages for part in message.parts
    )
    if (
        "discover_business_data" in returned_names
        and "query_business_evidence" not in returned_names
        and not has_retry
    ):
        invalid_query = {**QUERIES[0], "metrics": ["credit_limit"]}
        return ModelResponse(
            parts=[ToolCallPart("query_business_evidence", {"query": invalid_query})]
        )
    return _investigation_model(messages, info)


async def _stream_model(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[dict[int, DeltaToolCall]]:
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


async def _recovering_query_stream_model(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[dict[int, DeltaToolCall]]:
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


async def _interrupted_stream_model(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[dict[int, DeltaToolCall]]:
    returns = _returns(messages)
    returned_names = [part.tool_name for part in returns]
    if "discover_business_data" not in returned_names:
        part = ToolCallPart("discover_business_data", {})
    elif "query_business_evidence" not in returned_names:
        part = ToolCallPart("query_business_evidence", {"query": QUERIES[0]})
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

    assert contract.schema_version == "2.0"
    assert contract.discovery_source == "RULE"
    assert contract.entity_context["customer_id"] == "C015"
    assert contract.signals[0].signal_id == "hit-test"
    assert contract.data_quality.status == "UNKNOWN"


async def test_investigation_agent_discovers_and_queries_evidence(
    settings: Settings,
) -> None:
    outcome = await run_investigation_agent(
        settings, _case(), model=FunctionModel(stream_function=_stream_model)
    )

    assert outcome.partial is False
    assert [item.tool_name for item in outcome.evidence] == ["query_business_evidence"] * 4
    assert [item.arguments["dataset"] for item in outcome.evidence] == [
        "receivables",
        "sales_payments",
        "receivables",
        "contracts",
    ]
    assert outcome.report.evidence_completeness == "HIGH"
    assert outcome.report.risk_assessment.stage == "DETERIORATING"
    assert outcome.report.requires_human_review is True


async def test_investigation_agent_can_recover_from_invalid_controlled_query(
    settings: Settings,
) -> None:
    outcome = await run_investigation_agent(
        settings,
        _case(),
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
            settings, _case(), model=FunctionModel(stream_function=_stream_model)
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
        settings, _case(), model=FunctionModel(stream_function=_interrupted_stream_model)
    )

    assert outcome.partial is True
    assert len(outcome.evidence) == 1
    assert "无法判断" in outcome.report.investigation_summary
    assert outcome.report.hypotheses[0].status == "UNRESOLVED"
    assert outcome.report.facts[0].evidence_ids == [outcome.evidence[0].evidence_id]
    assert outcome.report.risk_assessment.stage == "LIMITED"

"""应用服务闭环测试。"""

import json
from collections.abc import AsyncIterator

import pytest
from ict_agent.config import Settings
from ict_agent.data import CaseStore, DuckDBStore
from ict_agent.models import PreTransactionSimulationRequest, ReviewRequest, ToolResult
from ict_agent.rules import RuleThresholds, build_rule_scan
from ict_agent.service import (
    _load_investigation_record,
    _load_protocol_snapshot,
    _protocol_response_summary,
    create_pre_transaction_simulation,
    get_case_detail,
    get_dashboard,
    get_investigation_protocol,
    get_investigation_protocol_detail,
    investigate_case,
    list_pre_transaction_simulations,
    review_case,
)
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

pytestmark = pytest.mark.anyio

QUERIES = [
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
        "dataset": "receivables",
        "grain": "order",
        "metrics": ["ar_amount", "overdue_amount", "max_overdue_days"],
        "time_window": "latest",
        "sort_by": "overdue_amount",
        "limit": 20,
    },
    {
        "dataset": "credit",
        "grain": "customer",
        "metrics": ["credit_limit", "list_status", "credit_insurance"],
        "time_window": "latest",
        "limit": 1,
    },
    {
        "dataset": "extensions",
        "grain": "order",
        "metrics": ["ar_amount", "overdue_amount", "matched_extension_actions"],
        "time_window": "all",
        "limit": 20,
    },
    {
        "dataset": "contracts",
        "grain": "contract",
        "metrics": ["contract_amount", "ar_amount", "overdue_amount"],
        "time_window": "latest",
        "limit": 20,
    },
]


def test_legacy_anthropic_protocol_is_not_exposed_as_current() -> None:
    legacy_protocol = json.dumps(
        {
            "schema_version": "3.0",
            "api_format": "anthropic_messages",
            "request": {"url": "https://api.deepseek.com/anthropic/v1/messages"},
        }
    )

    assert _load_protocol_snapshot(legacy_protocol) is None
    legacy_row = (
        "legacy-investigation",
        "INV|legacy",
        json.dumps({"trace": [{"tool_name": "query_business_evidence"}]}),
        json.dumps([{"tool_name": "query_business_evidence"}]),
        "2026-08-15T00:00:00Z",
        "3.0",
        "anthropic_messages",
    )
    assert _load_investigation_record(legacy_row) is None


def test_protocol_response_summary_collapses_streamed_reasoning() -> None:
    summary = _protocol_response_summary(
        {
            "status_code": 200,
            "headers": {"content-type": "text/event-stream"},
            "body": {
                "format": "sse",
                "events": [
                    {
                        "event": "message",
                        "data": {
                            "choices": [
                                {
                                    "delta": {"reasoning_content": "分析"},
                                    "finish_reason": None,
                                }
                            ]
                        },
                    },
                    {
                        "event": "message",
                        "data": {
                            "choices": [{"delta": {"content": "{}"}, "finish_reason": "stop"}],
                            "usage": {"completion_tokens": 2},
                        },
                    },
                    {"event": "message", "data": "[DONE]"},
                ],
            },
        }
    )

    assert summary is not None
    assert summary.body_format == "sse"
    assert summary.event_count == 3
    assert summary.reasoning_characters == 2
    assert summary.content_characters == 2
    assert summary.finish_reason == "stop"
    assert summary.usage == {"completion_tokens": 2}


def _service_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    returns = [
        part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)
    ]
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
        "investigation_summary": "工具证据确认存在逾期事实，具体原因仍需人工复核。",
        "risk_assessment": {
            "stage": "DETERIORATING",
            "statement": "逾期风险已经恶化，但最终回收结果无法判断。",
            "evidence_ids": [evidence_ids[0]],
            "drivers": ["应收历史存在持续未结清记录。"],
            "counter_signals": [],
            "watch_items": ["后续回款能否覆盖到期应收。"],
        },
        "hypotheses": [
            {
                "hypothesis_id": "H1",
                "statement": "现有数据支持长期应收尚未结清。",
                "status": "SUPPORTED",
                "supporting_evidence_ids": [evidence_ids[0]],
            }
        ],
        "facts": [{"statement": "已取得应收历史证据。", "evidence_ids": [evidence_ids[0]]}],
        "limitations": ["缺少外部处置记录。"],
        "recommended_priority": "HIGH",
        "recommended_actions": ["人工复核。"],
        "requires_human_review": True,
    }
    assert info.allow_text_output is True
    assert info.output_tools == []
    return ModelResponse(parts=[TextPart(json.dumps(output, ensure_ascii=False))])


async def _service_stream_model(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    response = _service_model(messages, info)
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


async def _service_partial_stream_model(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    returned_names = [
        part.tool_name
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    if "get_evidence" in returned_names:
        raise RuntimeError("simulated model interruption")
    response = _service_model(messages, info)
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


def _create_receivable_case(settings: Settings) -> str:
    draft = build_rule_scan(
        DuckDBStore(settings.database_path),
        RuleThresholds(
            deep_overdue_amount=100,
            deep_overdue_days=90,
            overdue_growth_amount=50,
            stale_inventory_amount=100,
            inventory_buildup_amount=500,
            inventory_slowdown_amount=500,
        ),
    )
    CaseStore(settings.case_database_path).save_rule_scan(draft.run, draft.cases, draft.hits)
    return next(case.case_id for case in draft.cases if case.case_type == "ACCOUNTS_RECEIVABLE")


def test_dashboard_does_not_require_model(settings: Settings) -> None:
    response = get_dashboard(settings=settings)

    assert response.latest_ar.period == "2026-07-31"
    assert response.inventory.period == "2026-06-30"


async def test_pre_transaction_simulation_enters_unified_case_queue(
    settings: Settings,
) -> None:
    simulation = await create_pre_transaction_simulation(
        PreTransactionSimulationRequest(
            customer_id="C015",
            business_type="DISTRIBUTION",
            scenario="ANOMALY",
            seed=17,
        ),
        settings=settings,
    )
    detail = get_case_detail(simulation.case_id, settings=settings)

    assert simulation.simulated is True
    assert simulation.amount_yuan > simulation.distribution_summary["p90_yuan"]
    assert detail.discovery_source == "PRE_TRANSACTION"
    assert detail.case_type == "PRE_TRANSACTION"
    assert detail.business_type == "DISTRIBUTION"
    assert detail.entity_context["simulated"] is True
    assert detail.data_quality.status == simulation.data_quality_status
    assert detail.signals[0].signal_code == "PRE_TRANSACTION_REVIEW"
    assert list_pre_transaction_simulations(settings=settings)[0].case_id == detail.case_id


async def test_investigation_service_persists_report(settings: Settings) -> None:
    case_id = _create_receivable_case(settings)
    model = FunctionModel(stream_function=_service_stream_model)

    record = await investigate_case(case_id, settings=settings, model=model)
    detail = get_case_detail(case_id, settings=settings)

    assert record.report.risk_assessment is not None
    assert len(record.evidence) == 6
    assert detail.status == "PENDING_HUMAN_REVIEW"
    assert detail.latest_investigation is not None
    assert record.protocol_available is True
    assert detail.latest_investigation.protocol_available is True
    assert "protocol_json" not in detail.model_dump_json()
    protocol = get_investigation_protocol(record.investigation_id, settings=settings)
    protocol_detail = get_investigation_protocol_detail(record.investigation_id, settings=settings)
    assert protocol.response is not None
    assert protocol_detail.request == protocol.request
    assert protocol_detail.response_summary is not None
    stored = CaseStore(settings.case_database_path).fetch_latest_investigation(case_id).rows[0]
    assert stored[5:] == ("4.0", "openai_chat_completions")

    await review_case(
        case_id,
        ReviewRequest(
            decision="CONFIRMED_RISK",
            reviewer="测试审核人",
            reason="证据支持风险成立。",
        ),
        settings=settings,
    )
    assert get_case_detail(case_id, settings=settings).status == "ACTION_IN_PROGRESS"


async def test_investigation_service_persists_partial_report(settings: Settings) -> None:
    case_id = _create_receivable_case(settings)
    model = FunctionModel(stream_function=_service_partial_stream_model)

    record = await investigate_case(case_id, settings=settings, model=model)
    detail = get_case_detail(case_id, settings=settings)

    assert "无法判断" in record.report.investigation_summary
    assert record.report.evidence_completeness == "LOW"
    assert record.report.risk_assessment is not None
    assert record.report.risk_assessment.stage == "LIMITED"
    assert len(record.evidence) == 1
    assert detail.status == "PENDING_HUMAN_REVIEW"
    assert detail.latest_investigation is not None

    await review_case(
        case_id,
        ReviewRequest(
            decision="NEEDS_MORE_EVIDENCE",
            reviewer="测试审核人",
            reason="现有证据不足，需要重新调查。",
        ),
        settings=settings,
    )
    assert get_case_detail(case_id, settings=settings).status == "PENDING_AGENT_REVIEW"

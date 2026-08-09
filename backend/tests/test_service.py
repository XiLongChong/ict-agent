"""应用服务闭环测试。"""

import json
from collections.abc import AsyncIterator

import pytest
from ict_agent.config import Settings
from ict_agent.data import CaseStore, DuckDBStore
from ict_agent.models import ToolResult
from ict_agent.rules import RuleThresholds, build_rule_scan
from ict_agent.service import get_case_detail, get_dashboard, investigate_case
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
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
]


def _service_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    returns = [
        part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)
    ]
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
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output)])


async def _service_stream_model(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[dict[int, DeltaToolCall]]:
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


async def _service_partial_stream_model(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[dict[int, DeltaToolCall]]:
    returned_names = [
        part.tool_name
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    if "query_business_evidence" in returned_names:
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


async def test_investigation_service_persists_report(settings: Settings) -> None:
    case_id = _create_receivable_case(settings)
    model = FunctionModel(stream_function=_service_stream_model)

    record = await investigate_case(case_id, settings=settings, model=model)
    detail = get_case_detail(case_id, settings=settings)

    assert record.report.risk_assessment is not None
    assert len(record.evidence) == 4
    assert detail.status == "PENDING_REVIEW"
    assert detail.latest_investigation is not None


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
    assert detail.status == "PENDING_REVIEW"
    assert detail.latest_investigation is not None

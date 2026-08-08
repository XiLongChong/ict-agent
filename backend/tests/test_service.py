"""应用服务闭环测试。"""

import pytest
from ict_agent.config import Settings
from ict_agent.data import CaseStore, DuckDBStore
from ict_agent.models import ChatRequest
from ict_agent.rules import RuleThresholds, build_rule_scan
from ict_agent.service import chat, get_case_detail, get_dashboard, investigate_case
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

pytestmark = pytest.mark.anyio


def _fake_model(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    has_result = any(
        isinstance(part, ToolReturnPart) for message in messages for part in message.parts
    )
    if has_result:
        return ModelResponse(parts=[TextPart(content="应收分析完成。")])
    return ModelResponse(parts=[ToolCallPart("get_latest_ar_summary", {})])


async def test_chat_service_returns_answer_and_evidence(settings: Settings) -> None:
    response = await chat(
        ChatRequest(message="最新应收是多少？"),
        settings=settings,
        model=FunctionModel(_fake_model),
    )

    assert response.answer == "应收分析完成。"
    assert response.evidence[0].period == "2026-07-31"
    assert len(response.request_id) == 32


def test_dashboard_does_not_require_model(settings: Settings) -> None:
    response = get_dashboard(settings=settings)

    assert response.latest_ar.period == "2026-07-31"
    assert response.inventory.period == "2026-06-30"


async def test_investigation_service_persists_report(settings: Settings) -> None:
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
    case_id = next(case.case_id for case in draft.cases if case.case_type == "ACCOUNTS_RECEIVABLE")
    model = TestModel(
        call_tools=["inspect_ar_history", "inspect_sales_and_payments"],
        custom_output_args={
            "investigation_summary": "完成应收调查。",
            "hypotheses": [
                {
                    "hypothesis_id": "H1",
                    "statement": "长期老账未清",
                    "status": "SUPPORTED",
                    "supporting_evidence_ids": [],
                    "contradicting_evidence_ids": [],
                    "missing_evidence": [],
                }
            ],
            "facts": [],
            "limitations": [],
            "recommended_priority": "HIGH",
            "recommended_actions": ["人工复核"],
            "requires_human_review": True,
        },
    )

    record = await investigate_case(case_id, settings=settings, model=model)
    detail = get_case_detail(case_id, settings=settings)

    assert record.report.investigation_summary == "完成应收调查。"
    assert detail.status == "PENDING_REVIEW"
    assert detail.latest_investigation is not None

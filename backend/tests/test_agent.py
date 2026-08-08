"""Pydantic AI 工具编排测试，不访问真实模型。"""

import pytest
from ict_agent.agent import run_analysis_agent, run_investigation_agent
from ict_agent.config import Settings
from ict_agent.models import RiskCaseDetail, RuleHit
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
    if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
        return ModelResponse(parts=[TextPart(content="已根据工具证据完成分析。")])
    return ModelResponse(parts=[ToolCallPart("get_latest_ar_summary", {})])


async def test_agent_records_real_tool_evidence(settings: Settings) -> None:
    outcome = await run_analysis_agent(
        settings,
        "最新应收是多少？",
        [],
        model=FunctionModel(_fake_model),
    )

    assert outcome.answer == "已根据工具证据完成分析。"
    assert len(outcome.evidence) == 1
    assert outcome.evidence[0].tool_name == "get_latest_ar_summary"
    assert outcome.evidence[0].sources == ["ar_snapshots"]


async def test_investigation_agent_uses_multiple_case_tools(settings: Settings) -> None:
    case = RiskCaseDetail(
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
    model = TestModel(
        call_tools=["inspect_ar_history", "inspect_sales_and_payments"],
        custom_output_args={
            "investigation_summary": "客户仍在回款，但老账需要人工复核。",
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
            "recommended_actions": ["人工复核长期老账"],
            "requires_human_review": True,
        },
    )

    outcome = await run_investigation_agent(settings, case, model=model)

    assert len(outcome.evidence) == 2
    assert outcome.report.evidence_completeness == "MEDIUM"
    assert outcome.report.requires_human_review is True

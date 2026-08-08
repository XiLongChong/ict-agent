"""DeepSeek Provider、Pydantic AI Agent 与工具注册。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Annotated
from uuid import uuid4

from pydantic import Field
from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.deepseek import DeepSeekProvider

from ict_agent import tools as analysis_tools
from ict_agent.config import OFFICIAL_DEEPSEEK_BASE_URL, Settings
from ict_agent.data import DuckDBStore
from ict_agent.models import (
    CaseType,
    ChatMessage,
    Evidence,
    InvestigationHypothesis,
    InvestigationReport,
    JsonScalar,
    RiskCaseDetail,
    ToolResult,
)
from ict_agent.prompts import AGENT_INSTRUCTIONS, INVESTIGATION_INSTRUCTIONS


@dataclass
class AgentDependencies:
    """单次运行的数据库和证据收集器。"""

    store: DuckDBStore
    evidence: list[Evidence] = field(default_factory=list)


@dataclass(frozen=True)
class AgentOutcome:
    """模型回答及本轮实际工具证据。"""

    answer: str
    evidence: list[Evidence]


@dataclass
class InvestigationDependencies:
    """单次案件调查的事实库、案件上下文和证据收集器。"""

    store: DuckDBStore
    case: RiskCaseDetail
    evidence: list[Evidence] = field(default_factory=list)


@dataclass(frozen=True)
class InvestigationOutcome:
    """结构化调查报告及本轮实际工具证据。"""

    report: InvestigationReport
    evidence: list[Evidence]


def _record_evidence(
    dependencies: AgentDependencies,
    tool_name: str,
    arguments: dict[str, JsonScalar],
    result: ToolResult,
) -> ToolResult:
    evidence_id = uuid4().hex
    dependencies.evidence.append(
        Evidence(
            evidence_id=evidence_id,
            tool_name=tool_name,
            arguments=arguments,
            sources=result.sources,
            period=result.period,
            summary=result.summary,
        )
    )
    return result.model_copy(update={"evidence_id": evidence_id})


def _record_investigation_evidence(
    dependencies: InvestigationDependencies,
    tool_name: str,
    result: ToolResult,
) -> ToolResult:
    evidence_id = uuid4().hex
    dependencies.evidence.append(
        Evidence(
            evidence_id=evidence_id,
            tool_name=tool_name,
            arguments={},
            sources=result.sources,
            period=result.period,
            summary=result.summary,
        )
    )
    return result.model_copy(update={"evidence_id": evidence_id})


def _create_agent(settings: Settings, model: Model | None = None) -> Agent[AgentDependencies, str]:
    if model is None:
        if settings.deepseek_base_url != OFFICIAL_DEEPSEEK_BASE_URL:
            raise ValueError("当前 Agent 只允许使用 DeepSeek 官方 Provider。")
        if settings.deepseek_api_key is None:
            raise ValueError("缺少 DeepSeek API Key。")
        provider = DeepSeekProvider(api_key=settings.deepseek_api_key.get_secret_value())
        model = OpenAIChatModel(settings.deepseek_model, provider=provider)

    agent = Agent[AgentDependencies, str](
        model,
        deps_type=AgentDependencies,
        instructions=AGENT_INSTRUCTIONS,
        model_settings=OpenAIChatModelSettings(
            temperature=0.1,
            max_tokens=1_200,
            thinking=False,
            parallel_tool_calls=False,
        ),
        retries=2,
    )

    @agent.tool
    def get_business_overview(ctx: RunContext[AgentDependencies]) -> ToolResult:
        """获取全数据窗口销售、毛利、回款、签约及最新应收和库存概览。"""

        result = analysis_tools.get_business_overview(ctx.deps.store)
        return _record_evidence(ctx.deps, "get_business_overview", {}, result)

    @agent.tool
    def get_latest_ar_summary(ctx: RunContext[AgentDependencies]) -> ToolResult:
        """获取最新月末应收余额、超期金额及 30/60 天以上超期率。"""

        result = analysis_tools.get_latest_ar_summary(ctx.deps.store)
        return _record_evidence(ctx.deps, "get_latest_ar_summary", {}, result)

    @agent.tool
    def get_ar_trend(ctx: RunContext[AgentDependencies]) -> ToolResult:
        """获取每个月末应收、超期、超期率和 60 天以上超期率趋势。"""

        result = analysis_tools.get_ar_trend(ctx.deps.store)
        return _record_evidence(ctx.deps, "get_ar_trend", {}, result)

    @agent.tool
    def get_customer_risk_profile(
        ctx: RunContext[AgentDependencies],
        customer_id: Annotated[
            str,
            Field(pattern=r"^C\d{3}$", description="客户编号，例如 C015"),
        ],
    ) -> ToolResult:
        """按客户编号获取授信名单、销售、回款、最新应收和展期画像。"""

        result = analysis_tools.get_customer_risk_profile(ctx.deps.store, customer_id)
        return _record_evidence(
            ctx.deps,
            "get_customer_risk_profile",
            {"customer_id": customer_id},
            result,
        )

    @agent.tool
    def get_inventory_health(ctx: RunContext[AgentDependencies]) -> ToolResult:
        """获取最新季末库存、库龄分桶和 180 天以上呆滞库存。"""

        result = analysis_tools.get_inventory_health(ctx.deps.store)
        return _record_evidence(ctx.deps, "get_inventory_health", {}, result)

    @agent.tool
    def get_project_progress(
        ctx: RunContext[AgentDependencies],
        contract_number: Annotated[
            str,
            Field(min_length=1, max_length=100, description="增值合同的正式合同编号"),
        ],
    ) -> ToolResult:
        """按正式合同号获取签约、出库、回款和最新应收闭环进度。"""

        result = analysis_tools.get_project_progress(ctx.deps.store, contract_number)
        return _record_evidence(
            ctx.deps,
            "get_project_progress",
            {"contract_number": contract_number},
            result,
        )

    return agent


@lru_cache(maxsize=1)
def _live_agent(settings: Settings) -> Agent[AgentDependencies, str]:
    return _create_agent(settings)


def _message_history(history: list[ChatMessage]) -> list[ModelMessage]:
    messages: list[ModelMessage] = []
    for item in history:
        if item.role == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(content=item.content)]))
        else:
            messages.append(ModelResponse(parts=[TextPart(content=item.content)]))
    return messages


async def run_analysis_agent(
    settings: Settings,
    message: str,
    history: list[ChatMessage],
    *,
    model: Model | None = None,
) -> AgentOutcome:
    """运行单次无状态分析，并返回真实工具证据。"""

    agent = _create_agent(settings, model) if model is not None else _live_agent(settings)
    dependencies = AgentDependencies(store=DuckDBStore(settings.database_path))
    result = await agent.run(
        message,
        deps=dependencies,
        message_history=_message_history(history),
        usage_limits=UsageLimits(request_limit=4, tool_calls_limit=4, output_tokens_limit=2_000),
    )
    return AgentOutcome(answer=result.output, evidence=list(dependencies.evidence))


def _create_investigation_agent(
    settings: Settings,
    case_type: CaseType,
    model: Model | None = None,
) -> Agent[InvestigationDependencies, InvestigationReport]:
    """按案件类型创建只暴露必要证据工具的调查 Agent。"""

    if model is None:
        if settings.deepseek_base_url != OFFICIAL_DEEPSEEK_BASE_URL:
            raise ValueError("当前 Agent 只允许使用 DeepSeek 官方 Provider。")
        if settings.deepseek_api_key is None:
            raise ValueError("缺少 DeepSeek API Key。")
        provider = DeepSeekProvider(api_key=settings.deepseek_api_key.get_secret_value())
        model = OpenAIChatModel(settings.deepseek_model, provider=provider)

    agent = Agent[InvestigationDependencies, InvestigationReport](
        model,
        output_type=InvestigationReport,
        deps_type=InvestigationDependencies,
        instructions=INVESTIGATION_INSTRUCTIONS,
        model_settings=OpenAIChatModelSettings(
            temperature=0.1,
            max_tokens=2_500,
            thinking=False,
            parallel_tool_calls=False,
        ),
        retries=2,
    )

    if case_type == "ACCOUNTS_RECEIVABLE":

        @agent.tool
        def inspect_ar_history(ctx: RunContext[InvestigationDependencies]) -> ToolResult:
            """检查该客户最近 12 个月应收、超期和深度超期的变化。"""

            customer_id = str(ctx.deps.case.entity_context["customer_id"])
            result = analysis_tools.get_customer_ar_history(ctx.deps.store, customer_id)
            return _record_investigation_evidence(ctx.deps, "inspect_ar_history", result)

        @agent.tool
        def inspect_sales_and_payments(
            ctx: RunContext[InvestigationDependencies],
        ) -> ToolResult:
            """按月检查该客户最近销售、回款、毛利和超期利息。"""

            customer_id = str(ctx.deps.case.entity_context["customer_id"])
            result = analysis_tools.get_customer_flow_history(ctx.deps.store, customer_id)
            return _record_investigation_evidence(ctx.deps, "inspect_sales_and_payments", result)

        @agent.tool
        def inspect_current_receivables(
            ctx: RunContext[InvestigationDependencies],
        ) -> ToolResult:
            """检查最新应收中金额最大的合同、订单、承诺日和超期明细。"""

            customer_id = str(ctx.deps.case.entity_context["customer_id"])
            result = analysis_tools.get_current_receivable_details(ctx.deps.store, customer_id)
            return _record_investigation_evidence(ctx.deps, "inspect_current_receivables", result)

        @agent.tool
        def inspect_extension_matches(
            ctx: RunContext[InvestigationDependencies],
        ) -> ToolResult:
            """按当前订单精确匹配历史展期，不用客户总次数替代。"""

            customer_id = str(ctx.deps.case.entity_context["customer_id"])
            result = analysis_tools.get_customer_extension_evidence(ctx.deps.store, customer_id)
            return _record_investigation_evidence(ctx.deps, "inspect_extension_matches", result)

        @agent.tool
        def inspect_credit_context(
            ctx: RunContext[InvestigationDependencies],
        ) -> ToolResult:
            """检查当前授信、名单时间、财务概况和信用保险。"""

            customer_id = str(ctx.deps.case.entity_context["customer_id"])
            result = analysis_tools.get_customer_credit_context(ctx.deps.store, customer_id)
            return _record_investigation_evidence(ctx.deps, "inspect_credit_context", result)

        @agent.tool
        def inspect_formal_contracts(
            ctx: RunContext[InvestigationDependencies],
        ) -> ToolResult:
            """检查可关联正式增值合同的签约、出库、回款和应收闭环。"""

            customer_id = str(ctx.deps.case.entity_context["customer_id"])
            result = analysis_tools.get_customer_contract_context(ctx.deps.store, customer_id)
            return _record_investigation_evidence(ctx.deps, "inspect_formal_contracts", result)

    else:

        @agent.tool
        def inspect_inventory_history(
            ctx: RunContext[InvestigationDependencies],
        ) -> ToolResult:
            """检查该物料与库存组织逐季库存、库龄和新老库存变化。"""

            material = str(ctx.deps.case.entity_context["material_code"])
            org = str(ctx.deps.case.entity_context["inventory_org"])
            result = analysis_tools.get_material_inventory_history(ctx.deps.store, material, org)
            return _record_investigation_evidence(ctx.deps, "inspect_inventory_history", result)

        @agent.tool
        def inspect_inventory_age_profile(
            ctx: RunContext[InvestigationDependencies],
        ) -> ToolResult:
            """检查最新季末各库龄区间和借物超期金额。"""

            material = str(ctx.deps.case.entity_context["material_code"])
            org = str(ctx.deps.case.entity_context["inventory_org"])
            result = analysis_tools.get_material_inventory_age_profile(
                ctx.deps.store, material, org
            )
            return _record_investigation_evidence(ctx.deps, "inspect_inventory_age_profile", result)

        @agent.tool
        def inspect_material_sales(
            ctx: RunContext[InvestigationDependencies],
        ) -> ToolResult:
            """检查最近销售速度、退货和毛利，区分需求补货与滞销。"""

            material = str(ctx.deps.case.entity_context["material_code"])
            org = str(ctx.deps.case.entity_context["inventory_org"])
            result = analysis_tools.get_material_sales_context(ctx.deps.store, material, org)
            return _record_investigation_evidence(ctx.deps, "inspect_material_sales", result)

    return agent


def _normalize_investigation_report(
    report: InvestigationReport,
    evidence: list[Evidence],
) -> InvestigationReport:
    valid_ids = {item.evidence_id for item in evidence}
    invalid_ids: set[str] = set()
    hypotheses: list[InvestigationHypothesis] = []
    for item in report.hypotheses:
        invalid_ids.update(set(item.supporting_evidence_ids) - valid_ids)
        invalid_ids.update(set(item.contradicting_evidence_ids) - valid_ids)
        hypotheses.append(
            item.model_copy(
                update={
                    "supporting_evidence_ids": [
                        value for value in item.supporting_evidence_ids if value in valid_ids
                    ],
                    "contradicting_evidence_ids": [
                        value for value in item.contradicting_evidence_ids if value in valid_ids
                    ],
                }
            )
        )
    facts = [
        fact.model_copy(
            update={"evidence_ids": [value for value in fact.evidence_ids if value in valid_ids]}
        )
        for fact in report.facts
    ]
    for fact in report.facts:
        invalid_ids.update(set(fact.evidence_ids) - valid_ids)
    limitations = list(report.limitations)
    if invalid_ids:
        limitations.append("模型引用了不存在的证据编号，系统已自动移除这些引用。")
    completeness = "HIGH" if len(evidence) >= 4 else "MEDIUM" if len(evidence) >= 2 else "LOW"
    return report.model_copy(
        update={
            "hypotheses": hypotheses,
            "facts": facts,
            "limitations": limitations,
            "evidence_completeness": completeness,
            "requires_human_review": True,
        }
    )


async def run_investigation_agent(
    settings: Settings,
    case: RiskCaseDetail,
    *,
    model: Model | None = None,
) -> InvestigationOutcome:
    """运行一次无历史污染的结构化案件调查。"""

    agent = _create_investigation_agent(settings, case.case_type, model)
    dependencies = InvestigationDependencies(store=DuckDBStore(settings.database_path), case=case)
    prompt = json.dumps(
        {
            "case_id": case.case_id,
            "case_type": case.case_type,
            "entity": case.entity_label,
            "observation_date": case.observation_date,
            "case_summary": case.summary,
            "rule_hits": [
                {
                    "rule_id": hit.rule_id,
                    "rule_name": hit.rule_name,
                    "reason": hit.reason,
                    "metrics": hit.metrics,
                }
                for hit in case.rule_hits
            ],
        },
        ensure_ascii=False,
    )
    result = await agent.run(
        prompt,
        deps=dependencies,
        usage_limits=UsageLimits(request_limit=8, tool_calls_limit=8, output_tokens_limit=4_000),
    )
    evidence = list(dependencies.evidence)
    if len(evidence) < 2:
        raise ValueError("调查 Agent 未取得至少两项独立工具证据。")
    report = _normalize_investigation_report(result.output, evidence)
    return InvestigationOutcome(report=report, evidence=evidence)

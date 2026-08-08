"""HTTP、Agent 与分析工具共用的数据契约。"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

type JsonScalar = str | int | float | bool | None
ChatRole = Literal["user", "assistant"]
CaseType = Literal["ACCOUNTS_RECEIVABLE", "INVENTORY"]
CaseStatus = Literal[
    "OPEN",
    "INVESTIGATING",
    "PENDING_REVIEW",
    "MONITORING",
    "ACTION_REQUIRED",
    "CLOSED_FALSE_POSITIVE",
    "CLOSED_RESOLVED",
]
RiskPriority = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
HypothesisStatus = Literal["SUPPORTED", "WEAKENED", "UNRESOLVED"]
EvidenceCompleteness = Literal["LOW", "MEDIUM", "HIGH"]
ReviewDecision = Literal["MONITOR", "ACTION_REQUIRED", "FALSE_POSITIVE", "RESOLVED"]


class ChatMessage(BaseModel):
    """由浏览器回传的精简文本历史。"""

    role: ChatRole
    content: Annotated[str, Field(min_length=1, max_length=2_000)]


class ChatRequest(BaseModel):
    """聊天请求。"""

    message: Annotated[str, Field(min_length=1, max_length=4_000)]
    history: Annotated[list[ChatMessage], Field(max_length=12)] = []


class ToolResult(BaseModel):
    """固定分析工具返回的可展示、可追溯结果。"""

    summary: Annotated[str, Field(min_length=1, max_length=3_000)]
    columns: Annotated[list[str], Field(min_length=1, max_length=30)]
    rows: Annotated[list[list[JsonScalar]], Field(max_length=200)]
    sources: Annotated[list[str], Field(min_length=1, max_length=7)]
    period: str
    metric_definitions: list[str] = []
    warnings: list[str] = []
    evidence_id: str | None = None

    @model_validator(mode="after")
    def validate_row_width(self) -> ToolResult:
        """保证每一行都能按 columns 直接渲染。"""

        expected = len(self.columns)
        if any(len(row) != expected for row in self.rows):
            raise ValueError("工具结果的每行列数必须与 columns 一致")
        return self


class Evidence(BaseModel):
    """一次真实工具调用留下的证据摘要。"""

    evidence_id: str = ""
    tool_name: str
    arguments: dict[str, JsonScalar]
    sources: list[str]
    period: str
    summary: str


class ChatResponse(BaseModel):
    """聊天响应。"""

    answer: str
    evidence: list[Evidence]
    request_id: str


class DashboardResponse(BaseModel):
    """首页所需的确定性分析结果，不消耗模型额度。"""

    overview: ToolResult
    latest_ar: ToolResult
    inventory: ToolResult
    ar_trend: ToolResult


class HealthResponse(BaseModel):
    """服务健康检查响应。"""

    status: Literal["ok"]
    service: str


class ErrorResponse(BaseModel):
    """对外稳定错误结构。"""

    error: str
    request_id: str


class RuleHit(BaseModel):
    """一条可审计的规则命中。"""

    rule_hit_id: str
    rule_id: str
    rule_name: str
    rule_version: str
    severity: RiskPriority
    exposure_amount: float
    reason: str
    metrics: dict[str, JsonScalar]
    threshold_source: str
    sources: list[str]
    period: str


class RiskCaseSummary(BaseModel):
    """案件队列中的单行摘要。"""

    case_id: str
    case_type: CaseType
    entity_type: str
    entity_id: str
    entity_label: str
    observation_date: str
    status: CaseStatus
    priority: RiskPriority
    exposure_amount: float
    summary: str
    rule_hit_count: int
    rule_set_version: str
    updated_at: str
    next_review_at: str | None = None


class InvestigationHypothesis(BaseModel):
    """Agent 对一个候选原因的证据判断。"""

    hypothesis_id: Annotated[str, Field(min_length=1, max_length=100)]
    statement: Annotated[str, Field(min_length=1, max_length=500)]
    status: HypothesisStatus
    supporting_evidence_ids: list[str] = []
    contradicting_evidence_ids: list[str] = []
    missing_evidence: list[str] = []


class InvestigationFact(BaseModel):
    """调查报告中的一条数据事实。"""

    statement: Annotated[str, Field(min_length=1, max_length=500)]
    evidence_ids: list[str] = []


class InvestigationReport(BaseModel):
    """调查 Agent 的结构化输出。"""

    investigation_summary: Annotated[str, Field(min_length=1, max_length=2_000)]
    hypotheses: Annotated[list[InvestigationHypothesis], Field(min_length=1, max_length=8)]
    facts: Annotated[list[InvestigationFact], Field(max_length=12)] = []
    limitations: Annotated[list[str], Field(max_length=12)] = []
    recommended_priority: RiskPriority
    recommended_actions: Annotated[list[str], Field(min_length=1, max_length=5)]
    evidence_completeness: EvidenceCompleteness = "LOW"
    requires_human_review: Literal[True] = True


class InvestigationRecord(BaseModel):
    """已经保存的一次调查。"""

    investigation_id: str
    case_id: str
    report: InvestigationReport
    evidence: list[Evidence]
    created_at: str


class ReviewRequest(BaseModel):
    """人工审核提交内容。"""

    decision: ReviewDecision
    reviewer: Annotated[str, Field(min_length=1, max_length=100)]
    reason: Annotated[str, Field(min_length=2, max_length=1_000)]
    action: Annotated[str | None, Field(max_length=1_000)] = None
    next_review_at: date | None = None

    @model_validator(mode="after")
    def monitoring_requires_review_date(self) -> ReviewRequest:
        if self.decision == "MONITOR" and self.next_review_at is None:
            raise ValueError("持续观察必须填写下一次复查日期")
        return self


class ReviewRecord(BaseModel):
    """已经保存的一次人工审核。"""

    review_id: str
    case_id: str
    decision: ReviewDecision
    reviewer: str
    reason: str
    action: str | None
    next_review_at: str | None
    created_at: str


class RiskCaseDetail(RiskCaseSummary):
    """案件详情、最新调查和审核历史。"""

    entity_context: dict[str, JsonScalar]
    rule_hits: list[RuleHit]
    latest_investigation: InvestigationRecord | None = None
    reviews: list[ReviewRecord] = []


class RuleRunResponse(BaseModel):
    """一次规则扫描的结果摘要。"""

    run_id: str
    rule_set_version: str
    observation_date: str
    cases_detected: int
    cases_created: int
    rule_hits: int
    receivable_cases: int
    inventory_cases: int
    created_at: str


class RiskOverviewResponse(BaseModel):
    """风险首页聚合。"""

    latest_run: RuleRunResponse | None
    total_cases: int
    open_cases: int
    pending_review_cases: int
    monitoring_cases: int
    action_required_cases: int
    critical_cases: int
    exposure_amount: float
    cases_by_type: dict[str, int]

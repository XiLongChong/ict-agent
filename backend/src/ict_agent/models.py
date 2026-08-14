"""HTTP、Agent 与分析工具共用的数据契约。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

type JsonScalar = str | int | float | bool | None
CaseType = Literal["ACCOUNTS_RECEIVABLE", "INVENTORY"]
CaseStatus = Literal[
    "PENDING_AGENT_REVIEW",
    "PENDING_HUMAN_REVIEW",
    "ACTION_IN_PROGRESS",
    "CLOSED",
]
RiskPriority = Literal["LOW", "MEDIUM", "HIGH"]
HypothesisStatus = Literal["SUPPORTED", "WEAKENED", "UNRESOLVED"]
RiskSignalStage = Literal["EARLY_WARNING", "DETERIORATING", "LIMITED"]
EvidenceCompleteness = Literal["LOW", "MEDIUM", "HIGH"]
ReviewDecision = Literal["CONFIRMED_RISK", "NEEDS_MORE_EVIDENCE", "NO_RISK"]
InvestigationToolName = Literal[
    "discover_evidence_capabilities",
    "search_business_records",
    "query_business_evidence",
]
DiscoverySource = Literal["RULE", "ANOMALY", "MANUAL"]
DataQualityStatus = Literal["PASS", "WARNING", "FAIL", "UNKNOWN"]
EvidenceDataset = Literal[
    "receivables",
    "sales_payments",
    "extensions",
    "credit",
    "contracts",
    "inventory",
    "sales",
]
EvidenceGrain = Literal[
    "customer",
    "month",
    "contract",
    "order",
    "quarter",
    "age_bucket",
]
EvidenceTimeWindow = Literal["latest", "last_3_months", "last_6_months", "last_12_months", "all"]
EvidenceSortDirection = Literal["asc", "desc"]
EvidenceMetric = Literal[
    "ar_amount",
    "overdue_amount",
    "overdue_30_amount",
    "overdue_60_amount",
    "overdue_rate",
    "overdue_60_rate",
    "max_overdue_days",
    "sales_amount",
    "payment_amount",
    "gross_profit",
    "overdue_interest",
    "max_payment_overdue_days",
    "matched_extension_actions",
    "credit_limit",
    "list_status",
    "credit_rating",
    "net_assets",
    "net_profit",
    "credit_insurance",
    "contract_amount",
    "invoiced_amount",
    "actual_margin_rate",
    "shipped_amount",
    "inventory_amount",
    "fresh_inventory_amount",
    "stale_inventory_amount",
    "weighted_age_days",
    "inventory_quantity",
    "overdue_loan_amount",
    "net_quantity",
    "return_amount",
    "gross_margin",
]
BusinessRecordType = Literal["customer", "contract", "order", "material"]
InvestigationTraceType = Literal[
    "TOOL_COMPLETED",
    "REPORT_VALIDATED",
    "PARTIAL_REPORT",
]
InvestigationStreamEventType = Literal[
    "RUN_STARTED",
    "TOOL_STARTED",
    "TOOL_COMPLETED",
    "VALIDATION_STARTED",
    "REPORT_COMPLETED",
    "ERROR",
]


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
    """一次真实调查工具调用留下的可复核证据。"""

    evidence_id: str = ""
    tool_name: InvestigationToolName
    arguments: dict[str, JsonScalar | list[JsonScalar]]
    sources: list[str]
    period: str
    summary: str
    columns: list[str] = []
    rows: list[list[JsonScalar]] = []
    metric_definitions: list[str] = []
    warnings: list[str] = []


class DatasetCapability(BaseModel):
    """Agent 可发现的一项只读业务数据能力。"""

    dataset: EvidenceDataset
    description: str
    grain: EvidenceGrain
    metrics: list[EvidenceMetric]
    time_windows: list[EvidenceTimeWindow]
    available: bool
    returned_rows: int = 0
    period: str | None = None
    limitations: list[str] = []


class BusinessDataCatalog(BaseModel):
    """当前案件可访问的数据地图，不包含数据库结构或 SQL。"""

    case_type: CaseType
    entity_scope: str
    observation_date: str
    datasets: list[DatasetCapability]
    global_rules: list[str]


class EvidenceQuery(BaseModel):
    """受控证据查询；所有选项都由后端语义层校验。"""

    dataset: EvidenceDataset
    grain: EvidenceGrain
    metrics: Annotated[list[EvidenceMetric], Field(min_length=1, max_length=12)]
    time_window: EvidenceTimeWindow = "latest"
    sort_by: EvidenceMetric | None = None
    sort_direction: EvidenceSortDirection = "desc"
    limit: Annotated[int, Field(ge=1, le=100)] = 30


class BusinessRecordSearchQuery(BaseModel):
    """在当前案件主体范围内搜索业务标识，不开放文件或数据库扫描。"""

    record_type: BusinessRecordType
    query: Annotated[str, Field(min_length=1, max_length=100)]
    limit: Annotated[int, Field(ge=1, le=30)] = 10


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


class DataSourceSnapshot(BaseModel):
    """数据快照中一张固定来源文件的可复核身份。"""

    table: str
    filename: str
    size_bytes: int
    sha256: str
    rows: int
    min_date: str | None
    max_date: str | None


class DataSnapshotResponse(BaseModel):
    """当前业务 DuckDB 对应的原始文件与模式身份。"""

    snapshot_id: str
    imported_at: str
    schema_fingerprint: str
    sources: list[DataSourceSnapshot]


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


class InvestigationSignalInput(BaseModel):
    """规则、异常雷达或人工入口交给调查内核的一条信号。"""

    signal_id: str
    signal_code: str
    signal_name: str
    reason: str
    severity: RiskPriority
    exposure_amount: float
    metrics: dict[str, JsonScalar]
    source_version: str
    threshold_source: str
    sources: list[str]
    period: str


class InvestigationDataQuality(BaseModel):
    """案件入口声明的数据质量状态；未知不得伪装成通过。"""

    status: DataQualityStatus = "UNKNOWN"
    warnings: list[str] = []


class InvestigationCaseInput(BaseModel):
    """规则引擎与 V2 调查内核之间冻结的输入契约。"""

    schema_version: Literal["2.0"] = "2.0"
    case_id: str
    discovery_source: DiscoverySource
    case_type: CaseType
    entity_type: str
    entity_id: str
    entity_label: str
    entity_context: dict[str, JsonScalar]
    observation_date: str
    priority: RiskPriority
    exposure_amount: float
    summary: str
    source_set_version: str
    signals: Annotated[list[InvestigationSignalInput], Field(min_length=1)]
    data_quality: InvestigationDataQuality


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
    risk_overview: str
    rule_hit_count: int
    rule_set_version: str
    updated_at: str


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


class RiskSignalAssessment(BaseModel):
    """把可判断的风险信号与仍待补证的根因、最终结果分开。"""

    stage: RiskSignalStage
    statement: Annotated[str, Field(min_length=1, max_length=500)]
    evidence_ids: Annotated[list[str], Field(min_length=1, max_length=9)]
    drivers: Annotated[list[str], Field(min_length=1, max_length=5)]
    counter_signals: Annotated[list[str], Field(max_length=5)] = []
    watch_items: Annotated[list[str], Field(min_length=1, max_length=5)]


class InvestigationTraceEvent(BaseModel):
    """保存到报告中的精简调查轨迹，不包含模型私有思维链。"""

    event_type: InvestigationTraceType
    title: Annotated[str, Field(min_length=1, max_length=200)]
    detail: Annotated[str, Field(min_length=1, max_length=1_000)]
    tool_name: InvestigationToolName | None = None
    evidence_id: str | None = None
    created_at: str


class InvestigationReport(BaseModel):
    """调查 Agent 的结构化输出。"""

    investigation_summary: Annotated[str, Field(min_length=1, max_length=2_000)]
    risk_assessment: RiskSignalAssessment
    hypotheses: Annotated[list[InvestigationHypothesis], Field(min_length=1, max_length=8)]
    facts: Annotated[list[InvestigationFact], Field(max_length=12)] = []
    limitations: Annotated[list[str], Field(max_length=12)] = []
    recommended_priority: RiskPriority
    recommended_actions: Annotated[list[str], Field(min_length=1, max_length=5)]
    evidence_completeness: EvidenceCompleteness = "LOW"
    requires_human_review: Literal[True] = True
    trace: Annotated[list[InvestigationTraceEvent], Field(max_length=30)] = []


class InvestigationRecord(BaseModel):
    """已经保存的一次调查。"""

    investigation_id: str
    case_id: str
    report: InvestigationReport
    evidence: list[Evidence]
    created_at: str


class InvestigationStreamEvent(BaseModel):
    """调查流式接口的一条 NDJSON 事件。"""

    sequence: Annotated[int, Field(ge=1)]
    event_type: InvestigationStreamEventType
    message: Annotated[str, Field(min_length=1, max_length=1_000)]
    tool_name: InvestigationToolName | None = None
    evidence: Evidence | None = None
    record: InvestigationRecord | None = None


class ReviewRequest(BaseModel):
    """人工审核提交内容。"""

    decision: ReviewDecision
    reviewer: Annotated[str, Field(min_length=1, max_length=100)]
    reason: Annotated[str, Field(min_length=2, max_length=1_000)]


class ReviewRecord(BaseModel):
    """已经保存的一次人工审核。"""

    review_id: str
    case_id: str
    decision: ReviewDecision
    reviewer: str
    reason: str
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
    pending_agent_cases: int
    pending_human_review_cases: int
    action_in_progress_cases: int
    closed_cases: int
    high_priority_cases: int
    exposure_amount: float
    cases_by_type: dict[str, int]


# ---------------------------------------------------------------------------
# 阶段 A：风险预警系统（健康度 / 名单建议 / 预警 / 项目）
# ---------------------------------------------------------------------------


class HealthDimension(BaseModel):
    """健康度的一个维度分数。"""

    key: str
    name: str
    score: float
    weight: float
    missing: bool = False


class HealthTrendPoint(BaseModel):
    """健康度趋势中的一个时点。"""

    period: str
    score: float


class HealthScoreResponse(BaseModel):
    """一条健康度评分。"""

    id: str
    subject_type: str
    subject_id: str
    subject_label: str
    score: float
    grade: str
    dimensions: list[HealthDimension] = []
    drivers: dict[str, list[str]] = {}
    trend: list[HealthTrendPoint] = []
    computed_at: str
    data_snapshot_id: str = ""


class ListRecommendationResponse(BaseModel):
    """一条名单调整建议。"""

    recommendation_id: str
    subject_type: str
    subject_id: str
    subject_label: str
    current_list: str
    target_list: str
    reason: str
    trigger_rule: str
    evidence: list[dict[str, str]] = []
    health_change: str
    risk_amount: float
    review_due_date: str
    status: str
    reviewer: str = ""
    review_reason: str = ""
    review_at: str = ""
    created_at: str


class ListRecommendationReviewRequest(BaseModel):
    """名单建议审批请求。"""

    decision: Literal["APPROVED", "REJECTED"]
    reviewer: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=2, max_length=1000)


class AlertResponse(BaseModel):
    """一条预警。"""

    alert_id: str
    alert_type: str
    subject_type: str
    subject_id: str
    subject_label: str
    severity: str
    message: str
    risk_amount: float
    status: str
    created_at: str
    related_id: str = ""


class ProjectViewResponse(BaseModel):
    """项目类视图（合同 + 模拟阶段/担保人 + 真实风险指标）。"""

    project_id: str
    name: str
    customer: str
    amount_wan: float
    amount_tier: str
    stage: str = ""
    planned_payment_date: str = ""
    milestone_progress: int = 0
    guarantor: str = ""
    paid_amount_wan: float = 0.0
    payment_rate: float | None = None
    overdue_rate: float | None = None
    margin_rate: float | None = None
    term_gap_days: int | None = None
    risk_level: str = "LOW"
    risk_note: str = ""
    credit_amount_wan: float | None = None
    simulated: bool = False


class PreAssessmentResponse(BaseModel):
    """事前评估结论。"""

    project_id: str
    name: str
    customer: str
    amount_wan: float
    amount_tier: str
    conclusion: str
    reasons: list[str]
    force_review: bool
    evaluated_at: str
    simulated: bool = True


class WarningOverviewResponse(BaseModel):
    """预警总览聚合（阶段 A 前端顶栏）。"""

    pre_assessment_pending: int
    in_process_alerts: int
    health_drop_count: int
    pending_list_recommendations: int
    high_risk_count: int
    risk_exposure: float
    grade_distribution: dict[str, int]
    pending_recommendations: list[ListRecommendationResponse] = []
    open_alerts: list[AlertResponse] = []


class FeishuStatusResponse(BaseModel):
    """飞书机器人配置、连接和群绑定状态。"""

    configured: bool
    connected: bool
    bound: bool


class FeishuTestResponse(BaseModel):
    """飞书测试消息发送结果。"""

    sent: bool
    message_id: str

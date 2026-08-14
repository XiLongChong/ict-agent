"""ICT Agent 的 FastAPI HTTP 入口。"""

from __future__ import annotations

import logging
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ict_agent.config import load_frontend_dist_dir
from ict_agent.models import (
    AlertResponse,
    CaseStatus,
    CaseType,
    DashboardResponse,
    DataSnapshotResponse,
    ErrorResponse,
    HealthResponse,
    HealthScoreResponse,
    ListRecommendationResponse,
    ListRecommendationReviewRequest,
    PreAssessmentResponse,
    ProjectViewResponse,
    ReviewRecord,
    ReviewRequest,
    RiskCaseDetail,
    RiskCaseSummary,
    RiskOverviewResponse,
    RuleRunResponse,
    SentimentResponse,
    SentimentVerifyRequest,
    WarningOverviewResponse,
)
from ict_agent.service import (
    ServiceError,
    acknowledge_alert,
    get_case_detail,
    get_dashboard,
    get_data_snapshot,
    get_health_score,
    get_risk_overview,
    list_alerts,
    list_cases,
    list_health_scores,
    list_projects_service,
    list_recommendations,
    list_sentiments_service,
    prepare_investigation,
    recalculate_health_scores,
    recover_interrupted_investigations,
    review_case,
    review_list_recommendation,
    run_pre_assessment_service,
    run_rule_scan,
    stream_prepared_investigation,
    verify_sentiment_service,
    warning_overview,
)

logger = logging.getLogger(__name__)
FRONTEND_DIST_DIR = load_frontend_dist_dir()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """启动时清理由上一个服务进程遗留的临时调查状态。"""

    recovered = recover_interrupted_investigations()
    if recovered:
        logger.warning("已恢复 %d 个被中断的 Agent 调查案件", recovered)
    yield


app = FastAPI(
    title="佳华智审风险调查 Agent API",
    version="0.4.0",
    description="基于可追溯七表快照、统一证据网关的可观察 Agent 调查与人工审核闭环。",
    lifespan=lifespan,
)


@app.exception_handler(ServiceError)
async def handle_service_error(_request: Request, exc: ServiceError) -> JSONResponse:
    """把应用错误映射为不泄漏内部细节的稳定响应。"""

    logger.warning(
        "request_id=%s service_error=%s status=%s",
        exc.request_id,
        type(exc.__cause__).__name__ if exc.__cause__ else type(exc).__name__,
        exc.status_code,
    )
    payload = ErrorResponse(error=str(exc), request_id=exc.request_id)
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump())


@app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """确认 HTTP 服务已经启动。"""

    return HealthResponse(status="ok", service="ict-agent")


@app.get(
    "/api/v1/data-snapshot",
    response_model=DataSnapshotResponse,
    responses={503: {"model": ErrorResponse}},
    tags=["system"],
)
async def data_snapshot() -> DataSnapshotResponse:
    """返回当前七表导入的可复核内容身份。"""

    return get_data_snapshot()


@app.get(
    "/api/v1/overview",
    response_model=DashboardResponse,
    responses={503: {"model": ErrorResponse}},
    tags=["analysis"],
)
async def overview() -> DashboardResponse:
    """返回首页经营、应收、库存和趋势数据。"""

    return get_dashboard()


@app.post(
    "/api/v1/rule-runs",
    response_model=RuleRunResponse,
    responses={500: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    tags=["risk-cases"],
)
async def create_rule_run() -> RuleRunResponse:
    """对当前最新快照执行一次幂等风险规则扫描。"""

    return run_rule_scan()


@app.get(
    "/api/v1/risk/overview",
    response_model=RiskOverviewResponse,
    responses={503: {"model": ErrorResponse}},
    tags=["risk-cases"],
)
async def risk_overview() -> RiskOverviewResponse:
    """返回案件数量、状态、敞口和最近扫描摘要。"""

    return get_risk_overview()


@app.get(
    "/api/v1/cases",
    response_model=list[RiskCaseSummary],
    responses={503: {"model": ErrorResponse}},
    tags=["risk-cases"],
)
async def cases(
    status: CaseStatus | None = None,
    case_type: CaseType | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> list[RiskCaseSummary]:
    """查询风险案件队列。"""

    return list_cases(status=status, case_type=case_type, limit=limit)


@app.get(
    "/api/v1/cases/{case_id}",
    response_model=RiskCaseDetail,
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    tags=["risk-cases"],
)
async def case_detail(case_id: str) -> RiskCaseDetail:
    """返回一个案件的规则、调查和人工审核详情。"""

    return get_case_detail(case_id)


@app.post(
    "/api/v1/cases/{case_id}/investigations",
    responses={
        200: {
            "description": "按行返回 InvestigationStreamEvent 的 NDJSON 事件流。",
            "content": {"application/x-ndjson": {}},
        },
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    tags=["agent"],
)
async def create_case_investigation(case_id: str) -> StreamingResponse:
    """流式返回 DeepSeek 的工具取证、校验和最终报告事件。"""

    prepared = prepare_investigation(case_id)

    async def ndjson_events() -> AsyncIterator[str]:
        async for event in stream_prepared_investigation(prepared):
            yield event.model_dump_json() + "\n"

    return StreamingResponse(ndjson_events(), media_type="application/x-ndjson")


@app.post(
    "/api/v1/cases/{case_id}/reviews",
    response_model=ReviewRecord,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    tags=["risk-cases"],
)
async def submit_case_review(case_id: str, request: ReviewRequest) -> ReviewRecord:
    """提交风险成立、需补充调查或确认无风险的人工复核结论。"""

    return review_case(case_id, request)


# ---------------------------------------------------------------------------
# 阶段 A：风险预警系统
# ---------------------------------------------------------------------------


@app.get(
    "/api/v1/health-scores",
    response_model=list[HealthScoreResponse],
    responses={503: {"model": ErrorResponse}},
    tags=["risk-warning"],
)
async def health_scores(
    subject_type: str | None = None,
    grade: str | None = None,
) -> list[HealthScoreResponse]:
    """返回健康度列表（可按类型/等级筛选）。"""

    return list_health_scores(subject_type=subject_type, grade=grade)


@app.get(
    "/api/v1/health-scores/{score_id}",
    response_model=HealthScoreResponse,
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    tags=["risk-warning"],
)
async def health_score_detail(score_id: str) -> HealthScoreResponse:
    """返回一条健康度详情。"""

    return get_health_score(score_id)


@app.post(
    "/api/v1/health-scores/recalculate",
    response_model=dict[str, int],
    responses={500: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    tags=["risk-warning"],
)
async def recalculate_health() -> dict[str, int]:
    """重算全部健康度并生成名单建议（确定性，不耗模型）。"""

    return recalculate_health_scores()


@app.get(
    "/api/v1/list-recommendations",
    response_model=list[ListRecommendationResponse],
    responses={503: {"model": ErrorResponse}},
    tags=["risk-warning"],
)
async def list_recommendations_api(
    status: str | None = None,
) -> list[ListRecommendationResponse]:
    """返回名单建议列表。"""

    return list_recommendations(status=status)


@app.post(
    "/api/v1/list-recommendations/{recommendation_id}/reviews",
    response_model=dict[str, object],
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    tags=["risk-warning"],
)
async def review_list_recommendation_api(
    recommendation_id: str, request: ListRecommendationReviewRequest
) -> dict[str, object]:
    """审批/驳回名单建议。"""

    return review_list_recommendation(recommendation_id, request)


@app.get(
    "/api/v1/alerts",
    response_model=list[AlertResponse],
    responses={503: {"model": ErrorResponse}},
    tags=["risk-warning"],
)
async def alerts(
    status: str | None = None,
    severity: str | None = None,
) -> list[AlertResponse]:
    """返回预警列表。"""

    return list_alerts(status=status, severity=severity)


@app.post(
    "/api/v1/alerts/{alert_id}/acknowledge",
    response_model=dict[str, object],
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    tags=["risk-warning"],
)
async def acknowledge_alert_api(alert_id: str) -> dict[str, object]:
    """确认一条预警。"""

    return acknowledge_alert(alert_id)


@app.get(
    "/api/v1/sentiments",
    response_model=list[SentimentResponse],
    responses={503: {"model": ErrorResponse}},
    tags=["risk-warning"],
)
async def sentiments() -> list[SentimentResponse]:
    """返回模拟舆情列表。"""

    return list_sentiments_service()


@app.post(
    "/api/v1/sentiments/{sentiment_id}/verify",
    response_model=SentimentResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    tags=["risk-warning"],
)
async def verify_sentiment_api(
    sentiment_id: str, request: SentimentVerifyRequest
) -> SentimentResponse:
    """核验舆情（确认/排除）并写留痕。"""

    return verify_sentiment_service(sentiment_id, request)


@app.get(
    "/api/v1/projects",
    response_model=list[ProjectViewResponse],
    responses={503: {"model": ErrorResponse}},
    tags=["risk-warning"],
)
async def projects() -> list[ProjectViewResponse]:
    """返回项目类视图（合同 + 模拟阶段/担保人）。"""

    return list_projects_service()


@app.post(
    "/api/v1/projects/{project_id}/pre-assessment/run",
    response_model=PreAssessmentResponse,
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    tags=["risk-warning"],
)
async def run_pre_assessment(project_id: str) -> PreAssessmentResponse:
    """对模拟新项目执行事前评估。"""

    return run_pre_assessment_service(project_id)


@app.get(
    "/api/v1/warning/overview",
    response_model=WarningOverviewResponse,
    responses={503: {"model": ErrorResponse}},
    tags=["risk-warning"],
)
async def warning_overview_api() -> WarningOverviewResponse:
    """返回预警总览聚合。"""

    return warning_overview()


@app.get("/", include_in_schema=False)
async def frontend_index() -> FileResponse:
    """提供同源的风险调查演示页面。"""

    return FileResponse(FRONTEND_DIST_DIR / "index.html")


@app.get("/risk", include_in_schema=False)
@app.get("/health", include_in_schema=False)
@app.get("/lists", include_in_schema=False)
@app.get("/sentiments", include_in_schema=False)
@app.get("/projects", include_in_schema=False)
@app.get("/cases", include_in_schema=False)
@app.get("/cases/{case_id}", include_in_schema=False)
@app.get("/business", include_in_schema=False)
async def frontend_route(case_id: str | None = None) -> FileResponse:
    """为已发布的 Vue history 路由提供同源入口。"""

    return FileResponse(FRONTEND_DIST_DIR / "index.html")


# Windows 上 mimetypes 会把 .js 误判为 text/plain，导致浏览器拒绝模块脚本。
# 显式注册正确的 MIME 类型，覆盖注册表错误映射。
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")

app.mount("/static", StaticFiles(directory=FRONTEND_DIST_DIR), name="static")

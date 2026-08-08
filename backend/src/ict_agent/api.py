"""ICT Agent 的 FastAPI HTTP 入口。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ict_agent.models import (
    CaseStatus,
    CaseType,
    ChatRequest,
    ChatResponse,
    DashboardResponse,
    ErrorResponse,
    HealthResponse,
    InvestigationRecord,
    ReviewRecord,
    ReviewRequest,
    RiskCaseDetail,
    RiskCaseSummary,
    RiskOverviewResponse,
    RuleRunResponse,
)
from ict_agent.service import (
    ServiceError,
    chat,
    get_case_detail,
    get_dashboard,
    get_risk_overview,
    investigate_case,
    list_cases,
    review_case,
    run_rule_scan,
)

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_DIR = PROJECT_ROOT / "frontend"

app = FastAPI(
    title="佳华智审风险调查 Agent API",
    version="0.2.0",
    description="基于 7 张比赛数据表的规则发现、Agent 调查与人工审核闭环。",
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
    "/api/v1/overview",
    response_model=DashboardResponse,
    responses={503: {"model": ErrorResponse}},
    tags=["analysis"],
)
async def overview() -> DashboardResponse:
    """返回首页经营、应收、库存和趋势数据。"""

    return get_dashboard()


@app.post(
    "/api/v1/chat",
    response_model=ChatResponse,
    responses={502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    tags=["agent"],
)
async def chat_endpoint(request: ChatRequest) -> ChatResponse:
    """让 DeepSeek 选择固定工具并回答数据问题。"""

    return await chat(request)


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
    response_model=InvestigationRecord,
    responses={
        404: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    tags=["agent"],
)
async def create_case_investigation(case_id: str) -> InvestigationRecord:
    """让 DeepSeek 针对指定案件分步取证并生成结构化调查报告。"""

    return await investigate_case(case_id)


@app.post(
    "/api/v1/cases/{case_id}/reviews",
    response_model=ReviewRecord,
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    tags=["risk-cases"],
)
async def submit_case_review(case_id: str, request: ReviewRequest) -> ReviewRecord:
    """提交人工审核、处置或持续观察决定。"""

    return review_case(case_id, request)


@app.get("/", include_in_schema=False)
async def frontend_index() -> FileResponse:
    """提供同源的风险调查演示页面。"""

    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

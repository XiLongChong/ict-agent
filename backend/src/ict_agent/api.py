"""ICT Agent 的 FastAPI HTTP 入口。"""

from __future__ import annotations

import logging
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ict_agent.config import load_frontend_dist_dir, load_settings
from ict_agent.feishu import start_feishu_bot, stop_feishu_bot
from ict_agent.models import (
    CaseStatus,
    CaseType,
    DashboardResponse,
    DataSnapshotResponse,
    ErrorResponse,
    FeishuStatusResponse,
    FeishuTestResponse,
    HealthResponse,
    InvestigationProtocolDetail,
    PreTransactionSimulationRequest,
    PreTransactionSimulationResponse,
    ReviewRecord,
    ReviewRequest,
    RiskCaseDetail,
    RiskCaseSummary,
    RiskOverviewResponse,
    RuleRunResponse,
)
from ict_agent.service import (
    ServiceError,
    create_pre_transaction_simulation,
    get_case_detail,
    get_dashboard,
    get_data_snapshot,
    get_feishu_status_service,
    get_investigation_protocol,
    get_investigation_protocol_detail,
    get_risk_overview,
    list_cases,
    list_pre_transaction_simulations,
    prepare_investigation,
    recover_interrupted_investigations,
    review_case,
    run_rule_scan,
    send_feishu_test_service,
    stream_prepared_investigation,
)

logger = logging.getLogger(__name__)
FRONTEND_DIST_DIR = load_frontend_dist_dir()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """恢复临时状态，并在已配置时管理飞书长连接。"""

    recovered = recover_interrupted_investigations()
    if recovered:
        logger.warning("已恢复 %d 个被中断的 Agent 调查案件", recovered)
    settings = load_settings(require_api_key=False, require_data_dir=False)
    await start_feishu_bot(settings)
    try:
        yield
    finally:
        await stop_feishu_bot()


app = FastAPI(
    title="佳华智审风险调查 Agent API",
    version="0.5.0",
    description="接收规则与事前交易信号、通过统一证据网关完成可观察调查和人工复核。",
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
    "/api/v1/integrations/feishu/status",
    response_model=FeishuStatusResponse,
    responses={503: {"model": ErrorResponse}},
    tags=["integrations"],
)
async def feishu_status() -> FeishuStatusResponse:
    """返回飞书机器人的配置、连接和通知群绑定状态。"""

    return get_feishu_status_service()


@app.post(
    "/api/v1/integrations/feishu/test",
    response_model=FeishuTestResponse,
    responses={409: {"model": ErrorResponse}},
    tags=["integrations"],
)
async def send_feishu_test() -> FeishuTestResponse:
    """向当前绑定通知群发送连通性测试卡片。"""

    return await send_feishu_test_service()


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

    return await run_rule_scan()


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


@app.get(
    "/api/v1/investigations/{investigation_id}/protocol",
    response_model=InvestigationProtocolDetail,
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    tags=["agent"],
)
async def investigation_protocol(investigation_id: str) -> InvestigationProtocolDetail:
    """按需返回完整请求和可安全渲染的响应摘要。"""

    return get_investigation_protocol_detail(investigation_id)


@app.get(
    "/api/v1/investigations/{investigation_id}/protocol/download",
    responses={
        200: {"content": {"application/json": {}}},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    tags=["agent"],
)
async def download_investigation_protocol(investigation_id: str) -> Response:
    """只在用户下载时序列化完整 DeepSeek HTTP 请求与响应。"""

    protocol = get_investigation_protocol(investigation_id)
    safe_id = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in investigation_id
    )[:64]
    filename = f"{safe_id or 'investigation'}-deepseek-chat-completions.json"
    return Response(
        content=protocol.model_dump_json(indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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

    return await review_case(case_id, request)


@app.get(
    "/api/v1/pre-transaction/simulations",
    response_model=list[PreTransactionSimulationResponse],
    responses={503: {"model": ErrorResponse}},
    tags=["pre-transaction"],
)
async def pre_transaction_simulations(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[PreTransactionSimulationResponse]:
    """返回最近基于真实历史分布生成的模拟新交易。"""

    return list_pre_transaction_simulations(limit=limit)


@app.post(
    "/api/v1/pre-transaction/simulations",
    response_model=PreTransactionSimulationResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    tags=["pre-transaction"],
)
async def simulate_pre_transaction(
    request: PreTransactionSimulationRequest,
) -> PreTransactionSimulationResponse:
    """生成模拟交易并创建统一的成交前调查案件。"""

    return await create_pre_transaction_simulation(request)


@app.get("/", include_in_schema=False)
async def frontend_index() -> FileResponse:
    """提供同源的风险调查演示页面。"""

    return FileResponse(FRONTEND_DIST_DIR / "index.html")


@app.get("/risk", include_in_schema=False)
@app.get("/pre-transaction", include_in_schema=False)
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

"""FastAPI HTTP 契约测试。"""

import re
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient
from ict_agent import api
from ict_agent.models import (
    InvestigationProtocolDetail,
    InvestigationProtocolResponseSummary,
    InvestigationProtocolSnapshot,
    InvestigationStreamEvent,
    PreTransactionSimulationResponse,
)
from pytest import MonkeyPatch

client = TestClient(api.app)


def test_health() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "ict-agent"}


def test_data_snapshot_contract(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        api,
        "get_data_snapshot",
        lambda: {
            "snapshot_id": "abc123",
            "imported_at": "2026-08-10T00:00:00+00:00",
            "schema_fingerprint": "fingerprint",
            "sources": [],
        },
    )

    response = client.get("/api/v1/data-snapshot")

    assert response.status_code == 200
    assert response.json()["snapshot_id"] == "abc123"


def test_frontend_is_served() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "风险调查工作台" in response.text
    assert "数据问答" not in response.text
    assets = re.findall(r'(?:src|href)="(/static/[^"]+)"', response.text)
    assert assets
    for asset in assets:
        assert client.get(asset).status_code == 200


def test_chat_api_is_removed() -> None:
    response = client.post("/api/v1/chat", json={"message": "最新应收？"})

    assert response.status_code == 404


def test_frontend_routes_serve_index() -> None:
    for path in (
        "/risk",
        "/cases",
        "/cases/demo-case-1",
        "/pre-transaction",
        "/business",
    ):
        response = client.get(path)

        assert response.status_code == 200
        assert "佳华智审" in response.text


def test_unknown_api_path_still_returns_json_404() -> None:
    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_unknown_frontend_path_is_not_hidden_by_fallback() -> None:
    assert client.get("/not-a-real-page").status_code == 404
    assert client.post("/risk").status_code == 405


def test_investigation_contract_streams_ndjson(monkeypatch: MonkeyPatch) -> None:
    async def fake_stream(_prepared: object) -> AsyncIterator[InvestigationStreamEvent]:
        yield InvestigationStreamEvent(
            sequence=1,
            event_type="RUN_STARTED",
            message="开始发现数据并调查证据。",
        )

    monkeypatch.setattr(api, "prepare_investigation", lambda _case_id: object())
    monkeypatch.setattr(api, "stream_prepared_investigation", fake_stream)
    response = client.post("/api/v1/cases/case-test/investigations")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.json()["event_type"] == "RUN_STARTED"


def test_investigation_protocol_is_loaded_and_downloaded_on_demand(
    monkeypatch: MonkeyPatch,
) -> None:
    request = {
        "method": "POST",
        "url": "https://api.deepseek.com/chat/completions",
        "headers": {"authorization": "[REDACTED]"},
        "body": {"model": "deepseek-v4-flash", "max_tokens": 16_000},
    }
    snapshot = InvestigationProtocolSnapshot(
        request_index=3,
        capture_source="wire",
        request=request,
        response={"status_code": 200, "headers": {}, "body": {"format": "sse"}},
    )
    detail = InvestigationProtocolDetail(
        request_index=3,
        capture_source="wire",
        request=request,
        response_summary=InvestigationProtocolResponseSummary(
            status_code=200,
            body_format="sse",
            event_count=5_003,
            finish_reason="length",
        ),
    )
    monkeypatch.setattr(api, "get_investigation_protocol_detail", lambda _id: detail)
    monkeypatch.setattr(api, "get_investigation_protocol", lambda _id: snapshot)

    response = client.get("/api/v1/investigations/inv-1/protocol")
    download = client.get("/api/v1/investigations/inv-1/protocol/download")

    assert response.status_code == 200
    assert response.json()["response_summary"]["event_count"] == 5_003
    assert "events" not in response.text
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment;")
    assert download.json()["response"]["body"]["format"] == "sse"


def test_pre_transaction_contract(monkeypatch: MonkeyPatch) -> None:
    async def fake_create(_request: object) -> PreTransactionSimulationResponse:
        return PreTransactionSimulationResponse(
            simulation_id="sim-1",
            case_id="pre-case-1",
            customer_id="C015",
            customer_name="测试客户",
            business_type="DISTRIBUTION",
            amount_yuan=120,
            proposed_term_days=40,
            expected_margin_rate=0.2,
            scenario="NORMAL",
            seed=1,
            historical_order_count=6,
            distribution_summary={
                "p25_yuan": 80,
                "median_yuan": 100,
                "p75_yuan": 110,
                "p90_yuan": 120,
            },
            source_snapshot_id="snapshot",
            data_quality_status="PASS",
            generated_at="2026-08-14T00:00:00+00:00",
        )

    monkeypatch.setattr(api, "create_pre_transaction_simulation", fake_create)
    response = client.post(
        "/api/v1/pre-transaction/simulations",
        json={"scenario": "NORMAL", "seed": 1},
    )

    assert response.status_code == 200
    assert response.json()["case_id"] == "pre-case-1"


def test_removed_parallel_risk_apis_are_not_in_openapi() -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/api/v1/health-scores" not in paths
    assert "/api/v1/lists" not in paths
    assert "/api/v1/projects" not in paths
    assert "/api/v1/alerts" not in paths

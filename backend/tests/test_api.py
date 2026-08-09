"""FastAPI HTTP 契约测试。"""

from collections.abc import AsyncIterator

from fastapi.testclient import TestClient
from ict_agent import api
from ict_agent.models import InvestigationStreamEvent
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


def test_chat_api_is_removed() -> None:
    response = client.post("/api/v1/chat", json={"message": "最新应收？"})

    assert response.status_code == 404


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

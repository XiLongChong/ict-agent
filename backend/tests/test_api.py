"""FastAPI HTTP 契约测试。"""

from fastapi.testclient import TestClient
from ict_agent import api
from ict_agent.models import ChatResponse, Evidence
from pytest import MonkeyPatch

client = TestClient(api.app)


def test_health() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "ict-agent"}


def test_frontend_is_served() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "风险调查工作台" in response.text


def test_chat_contract_without_real_model(monkeypatch: MonkeyPatch) -> None:
    async def fake_chat(_request: object) -> ChatResponse:
        return ChatResponse(
            answer="测试回答",
            evidence=[
                Evidence(
                    tool_name="get_latest_ar_summary",
                    arguments={},
                    sources=["ar_snapshots"],
                    period="2026-07-31",
                    summary="测试证据",
                )
            ],
            request_id="abc",
        )

    monkeypatch.setattr(api, "chat", fake_chat)
    response = client.post("/api/v1/chat", json={"message": "最新应收？", "history": []})

    assert response.status_code == 200
    assert response.json()["evidence"][0]["sources"] == ["ar_snapshots"]

"""飞书免登录状态与最小用户信息读取测试。"""

import json

import httpx
import pytest
from fastapi import Request
from ict_agent.config import Settings
from ict_agent.identity import (
    SESSION_COOKIE,
    ActorContext,
    FeishuAuthenticationError,
    actor_from_request,
    build_feishu_authorize_url,
    create_oauth_state,
    create_session_token,
    exchange_feishu_code,
    verify_oauth_state,
)
from pydantic import SecretStr


def _feishu_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "feishu_app_id": "cli_test",
            "feishu_app_secret": SecretStr("app-secret"),
            "public_base_url": "https://example.test",
        }
    )


def _request_with_cookie(cookie: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/session",
            "headers": [(b"cookie", f"{SESSION_COOKIE}={cookie}".encode())],
        }
    )


def test_signed_session_recovers_feishu_actor(settings: Settings) -> None:
    runtime = _feishu_settings(settings)
    actor = ActorContext(
        actor_type="FEISHU",
        display_name="张喜龙",
        open_id="ou_test",
        tenant_key="tenant_test",
    )

    restored = actor_from_request(
        _request_with_cookie(create_session_token(actor, runtime)), runtime
    )

    assert restored == actor
    assert actor_from_request(_request_with_cookie("tampered"), runtime).actor_type == "WEB_VISITOR"


def test_oauth_state_is_browser_bound_and_redirect_is_local(settings: Settings) -> None:
    runtime = _feishu_settings(settings)
    state = create_oauth_state(runtime, "browser-nonce", "//outside.example/path")

    assert verify_oauth_state(state, "browser-nonce", runtime) == "/"
    with pytest.raises(FeishuAuthenticationError):
        verify_oauth_state(state, "different-browser", runtime)
    authorize_url = build_feishu_authorize_url(runtime, state)
    assert authorize_url.startswith("https://accounts.feishu.cn/open-apis/authen/v1/authorize?")
    assert "client_id=cli_test" in authorize_url
    assert "client_secret" not in authorize_url


@pytest.mark.anyio
async def test_exchange_code_reads_only_minimum_feishu_identity(settings: Settings) -> None:
    runtime = _feishu_settings(settings)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/oauth/token"):
            payload = json.loads(request.content)
            assert payload["code"] == "one-time-code"
            return httpx.Response(200, json={"code": 0, "access_token": "user-token"})
        assert request.headers["authorization"] == "Bearer user-token"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "open_id": "ou_test",
                    "tenant_key": "tenant_test",
                    "name": "张喜龙",
                    "mobile": "must-not-be-stored",
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        actor = await exchange_feishu_code(runtime, "one-time-code", client=client)

    assert actor == ActorContext(
        actor_type="FEISHU",
        display_name="张喜龙",
        open_id="ou_test",
        tenant_key="tenant_test",
    )
    assert len(requests) == 2

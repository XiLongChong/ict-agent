"""飞书 OAuth 身份、短期登录态与匿名网页访问者识别。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Literal, cast
from urllib.parse import urlencode

import httpx
from fastapi import Request
from pydantic import JsonValue

from ict_agent.config import Settings

FEISHU_AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
FEISHU_USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"
SESSION_COOKIE = "ict_feishu_session"
OAUTH_NONCE_COOKIE = "ict_feishu_oauth_nonce"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
OAUTH_STATE_MAX_AGE_SECONDS = 10 * 60
ActorType = Literal["FEISHU", "WEB_VISITOR"]


class FeishuAuthenticationError(RuntimeError):
    """飞书登录参数无效、已过期或开放平台调用失败。"""


@dataclass(frozen=True)
class ActorContext:
    """一次业务操作对应的可信或匿名操作者。"""

    actor_type: ActorType
    display_name: str
    open_id: str | None = None
    tenant_key: str | None = None

    @property
    def authenticated(self) -> bool:
        return self.actor_type == "FEISHU"


WEB_VISITOR = ActorContext(actor_type="WEB_VISITOR", display_name="网页访客")


def _secret(settings: Settings) -> bytes:
    if settings.feishu_app_secret is None:
        raise FeishuAuthenticationError("飞书应用尚未配置，无法进行飞书登录。")
    return settings.feishu_app_secret.get_secret_value().encode("utf-8")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(payload: dict[str, JsonValue], settings: Settings, purpose: str) -> str:
    body = _b64encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
    signature = hmac.new(_secret(settings), f"{purpose}.{body}".encode(), hashlib.sha256).digest()
    return f"{body}.{_b64encode(signature)}"


def _verify(token: str, settings: Settings, purpose: str) -> dict[str, JsonValue]:
    try:
        body, supplied_signature = token.split(".", 1)
        expected = hmac.new(
            _secret(settings), f"{purpose}.{body}".encode(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _b64decode(supplied_signature)):
            raise ValueError("signature mismatch")
        payload = json.loads(_b64decode(body))
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError) as exc:
        raise FeishuAuthenticationError("飞书登录状态无效，请从应用入口重新打开。") from exc
    if not isinstance(payload, dict):
        raise FeishuAuthenticationError("飞书登录状态无效，请从应用入口重新打开。")
    expires_at = payload.get("exp")
    if not isinstance(expires_at, int | float) or expires_at < time.time():
        raise FeishuAuthenticationError("飞书登录状态已过期，请从应用入口重新打开。")
    return cast(dict[str, JsonValue], payload)


def sanitize_next_path(value: str | None) -> str:
    """只允许重定向回本站页面，避免开放重定向。"""

    if (
        not value
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ord(character) < 32 for character in value)
        or len(value) > 512
    ):
        return "/"
    return value


def create_oauth_state(settings: Settings, nonce: str, next_path: str | None) -> str:
    """创建绑定当前浏览器且带过期时间的 OAuth state。"""

    return _sign(
        {
            "nonce": nonce,
            "next": sanitize_next_path(next_path),
            "exp": int(time.time()) + OAUTH_STATE_MAX_AGE_SECONDS,
        },
        settings,
        "oauth-state",
    )


def verify_oauth_state(state: str, nonce: str | None, settings: Settings) -> str:
    """校验 OAuth state 与启动登录的浏览器一致。"""

    payload = _verify(state, settings, "oauth-state")
    stored_nonce = payload.get("nonce")
    if (
        not nonce
        or not isinstance(stored_nonce, str)
        or not hmac.compare_digest(stored_nonce, nonce)
    ):
        raise FeishuAuthenticationError("飞书登录请求无法核对，请从应用入口重新打开。")
    next_path = payload.get("next")
    return sanitize_next_path(next_path if isinstance(next_path, str) else None)


def oauth_redirect_uri(settings: Settings) -> str:
    """返回必须配置到飞书安全设置的固定回调 URL。"""

    if not settings.public_base_url:
        raise FeishuAuthenticationError("尚未配置 ICT_PUBLIC_BASE_URL，无法进行飞书登录。")
    return f"{settings.public_base_url.rstrip('/')}/api/v1/auth/feishu/callback"


def build_feishu_authorize_url(settings: Settings, state: str) -> str:
    """构造飞书 OAuth 2.0 授权地址；不申请额外用户数据权限。"""

    if settings.feishu_app_id is None:
        raise FeishuAuthenticationError("飞书应用尚未配置，无法进行飞书登录。")
    query = urlencode(
        {
            "client_id": settings.feishu_app_id,
            "redirect_uri": oauth_redirect_uri(settings),
            "response_type": "code",
            "state": state,
        }
    )
    return f"{FEISHU_AUTHORIZE_URL}?{query}"


async def exchange_feishu_code(
    settings: Settings,
    code: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> ActorContext:
    """交换一次性授权码并只读取姓名、open_id 与 tenant_key。"""

    if settings.feishu_app_id is None or settings.feishu_app_secret is None:
        raise FeishuAuthenticationError("飞书应用尚未配置，无法完成飞书登录。")
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=15)
    try:
        token_response = await http_client.post(
            FEISHU_TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": settings.feishu_app_id,
                "client_secret": settings.feishu_app_secret.get_secret_value(),
                "code": code,
                "redirect_uri": oauth_redirect_uri(settings),
            },
        )
        token_response.raise_for_status()
        token_payload = token_response.json()
        if not isinstance(token_payload, dict):
            raise FeishuAuthenticationError("飞书没有接受本次登录授权，请重新打开应用。")
        if token_payload.get("code") != 0 or not token_payload.get("access_token"):
            raise FeishuAuthenticationError("飞书没有接受本次登录授权，请重新打开应用。")
        user_response = await http_client.get(
            FEISHU_USER_INFO_URL,
            headers={"Authorization": f"Bearer {token_payload['access_token']}"},
        )
        user_response.raise_for_status()
        user_payload = user_response.json()
        if not isinstance(user_payload, dict):
            raise FeishuAuthenticationError("飞书用户信息读取失败，请重新打开应用。")
        data = user_payload.get("data")
        if user_payload.get("code") != 0 or not isinstance(data, dict):
            raise FeishuAuthenticationError("飞书用户信息读取失败，请重新打开应用。")
        open_id = data.get("open_id")
        if not isinstance(open_id, str) or not open_id or len(open_id) > 128:
            raise FeishuAuthenticationError("飞书没有返回有效的用户标识。")
        name = data.get("name")
        tenant_key = data.get("tenant_key")
        return ActorContext(
            actor_type="FEISHU",
            display_name=name[:100] if isinstance(name, str) and name else "飞书用户",
            open_id=open_id,
            tenant_key=(tenant_key[:128] if isinstance(tenant_key, str) and tenant_key else None),
        )
    except FeishuAuthenticationError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise FeishuAuthenticationError("飞书登录服务暂时不可用，请稍后重新打开应用。") from exc
    finally:
        if owns_client:
            await http_client.aclose()


def create_session_token(actor: ActorContext, settings: Settings) -> str:
    """为已经由飞书验证的用户创建短期同源登录态。"""

    if not actor.authenticated or actor.open_id is None:
        raise FeishuAuthenticationError("只有已验证的飞书用户可以创建登录态。")
    return _sign(
        {
            "open_id": actor.open_id,
            "name": actor.display_name,
            "tenant_key": actor.tenant_key or "",
            "exp": int(time.time()) + SESSION_MAX_AGE_SECONDS,
        },
        settings,
        "session",
    )


def actor_from_request(request: Request, settings: Settings) -> ActorContext:
    """从签名 Cookie 恢复飞书身份；缺失或失效时保持匿名网页访问。"""

    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return WEB_VISITOR
    try:
        payload = _verify(token, settings, "session")
    except FeishuAuthenticationError:
        return WEB_VISITOR
    open_id = payload.get("open_id")
    name = payload.get("name")
    tenant_key = payload.get("tenant_key")
    if not isinstance(open_id, str) or not open_id:
        return WEB_VISITOR
    return ActorContext(
        actor_type="FEISHU",
        display_name=name[:100] if isinstance(name, str) and name else "飞书用户",
        open_id=open_id[:128],
        tenant_key=tenant_key[:128] if isinstance(tenant_key, str) and tenant_key else None,
    )


def new_oauth_nonce() -> str:
    """生成绑定浏览器的不可预测 OAuth nonce。"""

    return secrets.token_urlsafe(32)

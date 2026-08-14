"""飞书机器人长连接、群绑定与消息卡片发送。"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import quote

from lark_oapi import Client, LogLevel  # type: ignore[import-untyped]
from lark_oapi.api.im.v1 import (  # type: ignore[import-untyped]
    CreateMessageRequest,
    CreateMessageRequestBody,
)
from lark_oapi.channel import Events, FeishuChannel  # type: ignore[import-untyped]
from lark_oapi.channel.types import InboundMessage  # type: ignore[import-untyped]

from ict_agent.config import Settings
from ict_agent.data import CaseStore

logger = logging.getLogger(__name__)
NOTIFICATION_CHAT_KEY = "feishu_notification_chat_id"
BIND_COMMAND = "绑定通知群"
CaseNotificationEvent = Literal[
    "RULE_SCAN_COMPLETED",
    "CASE_CREATED",
    "INVESTIGATION_COMPLETED",
    "PARTIAL_REPORT",
    "REVIEW_COMPLETED",
]


class FeishuIntegrationError(RuntimeError):
    """飞书集成尚未就绪或消息发送失败。"""


@dataclass(frozen=True)
class FeishuStatus:
    """不含密钥的飞书运行状态。"""

    configured: bool
    connected: bool
    bound: bool


@dataclass(frozen=True)
class CaseNotification:
    """案件流程通知的确定性输入。"""

    event_type: CaseNotificationEvent
    case_id: str
    case_type: str
    entity_label: str
    priority: str
    status: str
    summary: str
    business_type: str | None
    observation_date: str
    exposure_amount: float
    detail: str
    public_base_url: str | None = None


@dataclass(frozen=True)
class RuleScanNotification:
    """一次规则扫描的聚合通知，避免按案件刷屏。"""

    run_id: str
    observation_date: str
    cases_detected: int
    cases_created: int
    signal_count: int
    public_base_url: str | None = None


_EVENT_PRESENTATION: dict[CaseNotificationEvent, tuple[str, str]] = {
    "RULE_SCAN_COMPLETED": ("规则扫描完成", "blue"),
    "CASE_CREATED": ("新风险案件", "blue"),
    "INVESTIGATION_COMPLETED": ("案件调查完成", "green"),
    "PARTIAL_REPORT": ("案件调查中断", "orange"),
    "REVIEW_COMPLETED": ("人工复核完成", "purple"),
}


def build_rule_scan_notification_card(
    notification: RuleScanNotification,
) -> dict[str, object]:
    """构造一次扫描一张的聚合卡片。"""

    elements: list[dict[str, object]] = [
        {
            "tag": "markdown",
            "content": (
                f"**数据截至**：{notification.observation_date}\n"
                f"**候选案件**：{notification.cases_detected} 个\n"
                f"**本次新建**：{notification.cases_created} 个\n"
                f"**风险信号**：{notification.signal_count} 条"
            ),
        },
        {
            "tag": "markdown",
            "content": "规则命中只代表需要调查，不代表风险已经成立。",
        },
    ]
    if notification.public_base_url:
        url = f"{notification.public_base_url.rstrip('/')}/cases"
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开案件队列"},
                        "type": "primary",
                        "url": url,
                    }
                ],
            }
        )
    return {
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "规则扫描完成"},
        },
        "elements": elements,
    }


def build_case_notification_card(notification: CaseNotification) -> dict[str, object]:
    """构造不含内部执行信息的案件通知卡片。"""

    event_title, event_color = _EVENT_PRESENTATION[notification.event_type]
    priority = notification.priority or "—"
    business_type = notification.business_type or "—"
    elements: list[dict[str, object]] = [
        {"tag": "markdown", "content": f"**事件**：{event_title} · `{notification.event_type}`"},
        {
            "tag": "markdown",
            "content": f"**风险等级**：{priority}\n**主体**：{notification.entity_label}",
        },
        {
            "tag": "markdown",
            "content": f"**业务类型**：{business_type}\n**状态**：{notification.status}",
        },
        {
            "tag": "markdown",
            "content": (
                f"**数据截至**：{notification.observation_date}\n"
                f"**风险敞口**：{notification.exposure_amount:,.2f} 元"
            ),
        },
        {"tag": "markdown", "content": f"**摘要**：{notification.summary}\n{notification.detail}"},
    ]
    if notification.public_base_url:
        base = notification.public_base_url.rstrip("/")
        url = f"{base}/cases/{quote(notification.case_id, safe='')}"
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开案件详情"},
                        "type": "primary",
                        "url": url,
                    }
                ],
            }
        )
    return {
        "header": {
            "template": event_color,
            "title": {"tag": "plain_text", "content": event_title},
        },
        "elements": elements,
    }


def build_connection_card() -> dict[str, object]:
    """构造群绑定成功卡片。"""

    return {
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "佳华智审已接入"},
        },
        "elements": [
            {
                "tag": "markdown",
                "content": (
                    "**当前群已设为风险通知群**\n后续可接收规则扫描、AI 审查和事前评估结果。"
                ),
            }
        ],
    }


def build_test_card() -> dict[str, object]:
    """构造不含业务数据的连通性测试卡片。"""

    return {
        "header": {
            "template": "green",
            "title": {"tag": "plain_text", "content": "飞书通知测试成功"},
        },
        "elements": [
            {
                "tag": "markdown",
                "content": "佳华智审本地服务已能向当前群发送消息卡片。",
            }
        ],
    }


class FeishuBot:
    """管理一个企业自建应用机器人的长连接。"""

    def __init__(self, settings: Settings) -> None:
        if settings.feishu_app_id is None or settings.feishu_app_secret is None:
            raise FeishuIntegrationError("飞书机器人尚未配置。")
        self._store = CaseStore(settings.case_database_path)
        self._channel = FeishuChannel(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret.get_secret_value(),
            log_level=LogLevel.CRITICAL,
        )
        self._connected = False
        self._channel.on(Events.MESSAGE, self._handle_message)
        self._channel.on(Events.ERROR, self._handle_error)

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        """建立长连接并等待飞书确认就绪。"""

        await self._channel.connect_until_ready(timeout=30)
        self._connected = True
        logger.info("飞书机器人长连接已就绪")

    async def stop(self) -> None:
        """停止长连接。"""

        if self._connected:
            await self._channel.disconnect()
            await asyncio.sleep(0.1)
        self._connected = False

    async def _handle_message(self, message: InboundMessage) -> None:
        if message.chat_type != "group":
            await self._channel.send(
                message.chat_id,
                {"text": f"请把机器人加入群聊，再发送“@机器人 {BIND_COMMAND}”。"},
            )
            return
        if BIND_COMMAND not in message.content_text:
            await self._channel.send(
                message.chat_id,
                {"text": f"如需接收风险结果，请发送“@机器人 {BIND_COMMAND}”。"},
            )
            return
        self._store.save_integration_setting(
            NOTIFICATION_CHAT_KEY,
            message.chat_id,
            datetime.now(UTC).isoformat(),
        )
        await self._channel.send(message.chat_id, {"card": build_connection_card()})

    async def _handle_error(self, error: object) -> None:
        logger.error("飞书通道发生错误：%s", type(error).__name__)


_bot: FeishuBot | None = None
_bot_settings: Settings | None = None


async def start_feishu_bot(settings: Settings) -> None:
    """配置完整时启动全局机器人；失败不阻断核心调查服务。"""

    global _bot, _bot_settings
    _bot_settings = settings
    if settings.feishu_app_id is None or settings.feishu_app_secret is None:
        logger.info("未配置飞书机器人，跳过长连接")
        return
    bot = FeishuBot(settings)
    _bot = bot
    try:
        await bot.start()
    except Exception as exc:
        logger.error("飞书机器人长连接启动失败：%s", type(exc).__name__)


async def stop_feishu_bot() -> None:
    """停止全局机器人。"""

    global _bot, _bot_settings
    if _bot is not None:
        await _bot.stop()
    _bot = None
    _bot_settings = None


def get_feishu_status(settings: Settings) -> FeishuStatus:
    """返回配置、连接和通知群绑定状态。"""

    configured = settings.feishu_app_id is not None and settings.feishu_app_secret is not None
    bound = bool(
        CaseStore(settings.case_database_path).get_integration_setting(NOTIFICATION_CHAT_KEY)
    )
    return FeishuStatus(
        configured=configured,
        connected=_bot is not None and _bot.connected,
        bound=bound,
    )


async def send_feishu_test_card() -> str:
    """通过开放平台消息 API 发送测试卡片。"""

    return await _send_card(build_test_card(), "飞书测试消息发送失败")


async def send_feishu_case_notification(notification: CaseNotification) -> str:
    """通过开放平台消息 API 发送案件通知卡片。"""

    return await _send_card(
        build_case_notification_card(notification),
        "飞书案件通知发送失败",
    )


async def send_feishu_rule_scan_notification(notification: RuleScanNotification) -> str:
    """通过开放平台消息 API 发送规则扫描聚合卡片。"""

    return await _send_card(
        build_rule_scan_notification_card(notification),
        "飞书扫描通知发送失败",
    )


async def _send_card(card: dict[str, object], error_message: str) -> str:
    """发送交互卡片；出站通知不依赖接收群消息的长连接状态。"""

    if _bot_settings is None:
        raise FeishuIntegrationError("飞书机器人尚未配置或初始化。")
    settings = _bot_settings
    if settings.feishu_app_id is None or settings.feishu_app_secret is None:
        raise FeishuIntegrationError("飞书机器人尚未配置。")
    chat_id = CaseStore(settings.case_database_path).get_integration_setting(NOTIFICATION_CHAT_KEY)
    if not chat_id:
        raise FeishuIntegrationError(f"尚未绑定通知群，请先在群里 @{BIND_COMMAND}。")

    client = (
        Client.builder()
        .app_id(settings.feishu_app_id)
        .app_secret(settings.feishu_app_secret.get_secret_value())
        .log_level(LogLevel.CRITICAL)
        .build()
    )
    request = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("interactive")
            .content(json.dumps(card, ensure_ascii=False, separators=(",", ":")))
            .build()
        )
        .build()
    )
    try:
        response = await client.im.v1.message.acreate(request)
    except Exception as exc:
        logger.warning("飞书消息 API 调用异常：%s", type(exc).__name__)
        raise FeishuIntegrationError(f"{error_message}，请检查网络和应用配置。") from exc
    if not response.success():
        logger.warning(
            "飞书消息 API 拒绝请求：code=%s log_id=%s",
            response.code,
            response.get_log_id(),
        )
        raise FeishuIntegrationError(f"{error_message}，请检查机器人发消息权限和应用版本。")
    if response.data is None or not response.data.message_id:
        raise FeishuIntegrationError(f"{error_message}，飞书未返回消息编号。")
    return str(response.data.message_id)

"""飞书机器人长连接、群绑定与消息卡片发送。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from lark_oapi import LogLevel  # type: ignore[import-untyped]
from lark_oapi.channel import Events, FeishuChannel  # type: ignore[import-untyped]
from lark_oapi.channel.types import InboundMessage  # type: ignore[import-untyped]

from ict_agent.config import Settings
from ict_agent.data import CaseStore

logger = logging.getLogger(__name__)
NOTIFICATION_CHAT_KEY = "feishu_notification_chat_id"
BIND_COMMAND = "绑定通知群"


class FeishuIntegrationError(RuntimeError):
    """飞书集成尚未就绪或消息发送失败。"""


@dataclass(frozen=True)
class FeishuStatus:
    """不含密钥的飞书运行状态。"""

    configured: bool
    connected: bool
    bound: bool


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
                    "**当前群已设为风险通知群**\n"
                    "后续可接收规则扫描、AI 审查、舆情核验和事前评估结果。"
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

    async def send_test_card(self) -> str:
        """向已绑定群发送一张测试卡片并返回消息编号。"""

        chat_id = self._store.get_integration_setting(NOTIFICATION_CHAT_KEY)
        if not chat_id:
            raise FeishuIntegrationError(f"尚未绑定通知群，请先在群里 @{BIND_COMMAND}。")
        result = await self._channel.send(chat_id, {"card": build_test_card()})
        if not result.success:
            raise FeishuIntegrationError("飞书测试消息发送失败，请检查应用版本和机器人权限。")
        return result.message_id or ""

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


async def start_feishu_bot(settings: Settings) -> None:
    """配置完整时启动全局机器人；失败不阻断核心调查服务。"""

    global _bot
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

    global _bot
    if _bot is not None:
        await _bot.stop()
    _bot = None


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
    """通过全局机器人发送测试卡片。"""

    if _bot is None or not _bot.connected:
        raise FeishuIntegrationError("飞书机器人长连接尚未就绪。")
    return await _bot.send_test_card()

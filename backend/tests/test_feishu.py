"""飞书适配层的确定性行为测试。"""

from datetime import UTC, datetime
from pathlib import Path

from ict_agent.data import CaseStore
from ict_agent.feishu import (
    NOTIFICATION_CHAT_KEY,
    build_connection_card,
    build_test_card,
)


def test_feishu_cards_do_not_contain_credentials() -> None:
    connection_card = build_connection_card()
    test_card = build_test_card()

    assert connection_card["header"] == {
        "template": "blue",
        "title": {"tag": "plain_text", "content": "佳华智审已接入"},
    }
    assert test_card["header"] == {
        "template": "green",
        "title": {"tag": "plain_text", "content": "飞书通知测试成功"},
    }
    assert "secret" not in repr((connection_card, test_card)).lower()


def test_notification_chat_binding_is_replaced_atomically(tmp_path: Path) -> None:
    store = CaseStore(tmp_path / "cases.duckdb")

    store.save_integration_setting(
        NOTIFICATION_CHAT_KEY,
        "oc_first",
        datetime.now(UTC).isoformat(),
    )
    store.save_integration_setting(
        NOTIFICATION_CHAT_KEY,
        "oc_second",
        datetime.now(UTC).isoformat(),
    )

    assert store.get_integration_setting(NOTIFICATION_CHAT_KEY) == "oc_second"

"""飞书适配层的确定性行为测试。"""

from datetime import UTC, datetime
from pathlib import Path

from ict_agent.data import CaseStore
from ict_agent.feishu import (
    NOTIFICATION_CHAT_KEY,
    CaseNotification,
    RuleScanNotification,
    build_case_notification_card,
    build_connection_card,
    build_rule_scan_notification_card,
    build_test_card,
)


def _notification(
    event_type: str, *, url: str | None = "https://example.test/app"
) -> CaseNotification:
    return CaseNotification(
        event_type=event_type,  # type: ignore[arg-type]
        case_id="case/001?x=1",
        investigation_profile="RECEIVABLES",
        subject_label="示例客户",
        priority="HIGH",
        status="PENDING_HUMAN_REVIEW",
        summary="应收风险需要人工复核",
        business_type="分销",
        observation_date="2026-08-14",
        exposure_amount=1234.5,
        detail="请打开案件详情查看可追溯证据。",
        public_base_url=url,
    )


def test_case_notification_cards_cover_all_events_and_stable_priority() -> None:
    expected = {
        "CASE_CREATED": ("新风险案件", "blue"),
        "INVESTIGATION_COMPLETED": ("案件调查完成", "green"),
        "PARTIAL_REPORT": ("案件调查中断", "orange"),
        "REVIEW_COMPLETED": ("人工复核完成", "purple"),
    }
    for event_type, (title, color) in expected.items():
        card = build_case_notification_card(_notification(event_type))
        assert card["header"] == {
            "template": color,
            "title": {"tag": "plain_text", "content": title},
        }
        rendered = repr(card)
        assert "HIGH" in rendered
        assert "示例客户" in rendered
        assert "分销" in rendered
        assert "PENDING_HUMAN_REVIEW" in rendered


def test_case_notification_card_encodes_case_link() -> None:
    card = build_case_notification_card(_notification("CASE_CREATED"))
    action = card["elements"][-1]
    assert action["actions"][0]["url"] == "https://example.test/app/cases/case%2F001%3Fx%3D1"


def test_case_notification_card_without_link_has_no_action() -> None:
    card = build_case_notification_card(_notification("PARTIAL_REPORT", url=None))
    assert all(element["tag"] != "action" for element in card["elements"])


def test_case_notification_card_contains_no_sensitive_execution_fields() -> None:
    card = build_case_notification_card(_notification("REVIEW_COMPLETED"))
    rendered = repr(card).lower()
    assert "select " not in rendered
    assert "sql" not in rendered
    assert "secret" not in rendered
    assert "api_key" not in rendered
    assert "思维链" not in rendered


def test_rule_scan_notification_is_aggregated() -> None:
    card = build_rule_scan_notification_card(
        RuleScanNotification(
            run_id="run-1",
            observation_date="2026-07-31",
            cases_detected=88,
            cases_created=82,
            signal_count=140,
            public_base_url="https://example.test/app",
        )
    )
    rendered = repr(card)

    assert "规则扫描完成" in rendered
    assert "88" in rendered and "82" in rendered and "140" in rendered
    assert card["elements"][-1]["actions"][0]["url"] == "https://example.test/app/cases"


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

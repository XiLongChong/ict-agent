"""名单建议引擎测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from ict_agent.data import CaseStore
from ict_agent.listmgmt import (
    build_recommendations,
    list_label,
    review_recommendation,
)


@pytest.fixture
def case_store(tmp_path: Path) -> CaseStore:
    store = CaseStore(tmp_path / "cases.duckdb")
    store.ensure_ready()
    return store


def _health(
    subject_id: str,
    *,
    score: float,
    grade: str,
    trend: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "subject_type": "CUSTOMER",
        "subject_id": subject_id,
        "subject_label": f"客户{subject_id}",
        "score": score,
        "grade": grade,
        "dimensions": [],
        "drivers": {"down": ["应收超期率偏高"], "up": []},
        "trend": trend
        or [{"period": "2026-06", "score": 75.0}, {"period": "2026-07", "score": 55.0}],
        "computed_at": "2026-08-01T00:00:00+00:00",
    }


def test_list_label() -> None:
    assert list_label("WHITE") == "白名单"
    assert list_label("WATCH") == "观察中"
    assert list_label("BLACK") == "黑名单"
    assert list_label("GENERAL") == "一般"
    assert list_label("UNKNOWN") == "UNKNOWN"


def test_warning_generates_watch_recommendation(case_store: CaseStore) -> None:
    """健康度 WARNING 且当前白名单/一般 → 建议进入观察中。"""

    health = [_health("C001", score=55.0, grade="WARNING")]
    current = {"C001": "WHITE"}
    written = build_recommendations(
        case_store, health, current_list_map=current, now="2026-08-01T00:00:00+00:00"
    )
    assert len(written) == 1
    item = written[0]
    assert item["subject_id"] == "C001"
    assert item["current_list"] == "WHITE"
    assert item["target_list"] == "WATCH"
    assert item["status"] == "PENDING"
    assert item["health_change"].startswith("较上期")


def test_idempotent_no_duplicate(case_store: CaseStore) -> None:
    """同一客户重复生成建议不重复（幂等）。"""

    health = [_health("C002", score=30.0, grade="HIGH_RISK")]
    current = {"C002": "GENERAL"}
    build_recommendations(
        case_store, health, current_list_map=current, now="2026-08-01T00:00:00+00:00"
    )
    build_recommendations(
        case_store, health, current_list_map=current, now="2026-08-02T00:00:00+00:00"
    )
    rows = case_store.fetch_list_recommendations(status="PENDING").rows
    pending = [r for r in rows if str(r[0]) == "C002" or str(r[2]) == "C002"]
    assert len(pending) == 1


def test_blacklist_not_upgraded(case_store: CaseStore) -> None:
    """黑名单客户不自动升级。"""

    health = [_health("C003", score=20.0, grade="HIGH_RISK")]
    current = {"C003": "BLACK"}
    written = build_recommendations(case_store, health, current_list_map=current)
    assert written == []


def test_recovered_suggests_white(case_store: CaseStore) -> None:
    """健康度恢复 → 建议进入白名单（观察中/一般 -> 白名单）。"""

    health = [_health("C004", score=88.0, grade="HEALTHY")]
    current = {"C004": "WATCH"}
    written = build_recommendations(case_store, health, current_list_map=current)
    assert len(written) == 1
    assert written[0]["target_list"] == "WHITE"


def test_approve_writes_audit(case_store: CaseStore) -> None:
    """APPROVED 且名单变化时写审计记录。"""

    health = [_health("C005", score=45.0, grade="WARNING")]
    current = {"C005": "WHITE"}
    written = build_recommendations(case_store, health, current_list_map=current)
    assert len(written) == 1
    recommendation_id = written[0]["recommendation_id"]

    result = review_recommendation(
        case_store,
        recommendation_id,
        decision="APPROVED",
        reviewer="张风控",
        reason="确认风险，转入观察中",
        now="2026-08-01T00:00:00+00:00",
    )
    assert result["status"] == "APPROVED"
    assert result["subject_id"] == "C005"

    # 审计记录已写入
    changes = case_store._fetch(
        "SELECT change_id, subject_id, from_list, to_list, approver FROM list_changes"
    ).rows
    assert len(changes) == 1
    assert changes[0][1] == "C005"
    assert changes[0][2] == "WHITE"
    assert changes[0][3] == "WATCH"
    assert changes[0][4] == "张风控"


def test_reject_no_audit(case_store: CaseStore) -> None:
    """REJECTED 不写审计。"""

    health = [_health("C006", score=45.0, grade="WARNING")]
    current = {"C006": "GENERAL"}
    written = build_recommendations(case_store, health, current_list_map=current)
    recommendation_id = written[0]["recommendation_id"]

    result = review_recommendation(
        case_store,
        recommendation_id,
        decision="REJECTED",
        reviewer="李风控",
        reason="证据不足，暂不调整",
        now="2026-08-01T00:00:00+00:00",
    )
    assert result["status"] == "REJECTED"
    changes = case_store._fetch("SELECT COUNT(*) FROM list_changes").rows[0][0]
    assert changes == 0


def test_review_missing_raises_key_error(case_store: CaseStore) -> None:
    """建议不存在时抛 KeyError（service 映射 404）。"""

    with pytest.raises(KeyError):
        review_recommendation(
            case_store,
            "NOPE",
            decision="APPROVED",
            reviewer="张风控",
            reason="测试不存在建议",
            now="2026-08-01T00:00:00+00:00",
        )


def test_review_already_processed_raises_value_error(case_store: CaseStore) -> None:
    """已处理建议重复审批时抛 ValueError（service 映射 409）。"""

    health = [_health("C007", score=45.0, grade="WARNING")]
    current = {"C007": "GENERAL"}
    written = build_recommendations(case_store, health, current_list_map=current)
    recommendation_id = written[0]["recommendation_id"]

    review_recommendation(
        case_store,
        recommendation_id,
        decision="APPROVED",
        reviewer="张风控",
        reason="首次审批通过",
        now="2026-08-01T00:00:00+00:00",
    )
    with pytest.raises(ValueError):
        review_recommendation(
            case_store,
            recommendation_id,
            decision="APPROVED",
            reviewer="张风控",
            reason="重复审批应被拒绝",
            now="2026-08-02T00:00:00+00:00",
        )

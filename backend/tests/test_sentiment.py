"""舆情服务与项目事前评估测试（模拟数据，不触碰真实业务库）。"""

from __future__ import annotations

from pathlib import Path

import duckdb
from ict_agent.data import CaseStore, DuckDBStore
from ict_agent.project import amount_tier, list_new_projects, list_projects, run_pre_assessment
from ict_agent.sentiment import active_negative_sentiments, list_sentiments, verify_sentiment
from ict_agent.simdata import (
    SimulatedData,
    SimulatedNewProject,
    SimulatedSentiment,
)


def _sentiment(
    sentiment_id: str = "S2026-001",
    *,
    title: str = "网传担保人失联",
    severity: str = "重大",
    verify_status: str = "PENDING",
    event_type: str = "失联",
) -> SimulatedSentiment:
    return SimulatedSentiment(
        sentiment_id=sentiment_id,
        title=title,
        source="网络新闻",
        published_at="2026-08-01",
        subject_type="担保人",
        subject="陈志远",
        event_type=event_type,
        severity=severity,
        impact_amount_wan=700.0,
        verify_status=verify_status,
        related_project="项目P0805",
        process_status="处理中",
    )


def _new_project(
    project_id: str = "P2026-101",
    *,
    customer_list: str = "一般",
    amount_wan: float = 300.0,
    customer_id: str = "C001",
    customer_name: str = "北京澜图科技有限公司",
) -> SimulatedNewProject:
    return SimulatedNewProject(
        project_id=project_id,
        project_name="测试项目",
        customer_id=customer_id,
        customer_name=customer_name,
        customer_list=customer_list,
        project_amount_wan=amount_wan,
        amount_tier=amount_tier(amount_wan),
        credit_amount_wan=amount_wan,
        guarantor="测试担保人",
        applied_at="2026-08-05",
        planned_payment_date="2026-12-31",
        note="",
    )


def _sim(
    *sentiments: SimulatedSentiment, new_projects: list[SimulatedNewProject] | None = None
) -> SimulatedData:
    return SimulatedData(
        project_stages=(),
        guarantors=(),
        sentiments=tuple(sentiments),
        new_projects=tuple(new_projects or ()),
    )


# ---------------------------------------------------------------------------
# 舆情服务
# ---------------------------------------------------------------------------


def test_list_sentiments_returns_all_with_verify_label() -> None:
    sim = _sim(
        _sentiment(verify_status="PENDING"),
        _sentiment("S2026-002", severity="高", verify_status="CONFIRMED", event_type="诉讼"),
        _sentiment("S2026-003", severity="中", verify_status="EXCLUDED", event_type="停业传闻"),
    )

    rows = list_sentiments(sim)

    assert len(rows) == 3
    by_id = {str(row["sentiment_id"]): row for row in rows}
    assert by_id["S2026-001"]["verify_status"] == "PENDING"
    assert by_id["S2026-001"]["verify_label"] == "待核验"
    assert by_id["S2026-002"]["verify_label"] == "已确认"
    assert by_id["S2026-003"]["verify_label"] == "已排除"
    assert all(row["simulated"] is True for row in rows)


def test_active_negative_sentiments_excludes_excluded_and_positive() -> None:
    sim = _sim(
        _sentiment("S2026-001", verify_status="PENDING"),
        _sentiment("S2026-002", verify_status="EXCLUDED"),
        _sentiment("S2026-003", verify_status="CONFIRMED", event_type="正面报道"),
    )

    rows = active_negative_sentiments(sim)

    assert [str(row["sentiment_id"]) for row in rows] == ["S2026-001"]


def test_verify_confirmed_writes_alert_and_notification(
    tmp_path: Path,
) -> None:
    sim = _sim(_sentiment(verify_status="PENDING", severity="重大"))
    store = CaseStore(tmp_path / "cases.duckdb")

    updated = verify_sentiment(
        sim,
        "S2026-001",
        decision="CONFIRMED",
        verifier="风控专员",
        now="2026-08-13T10:00:00",
        store=store,
    )

    assert updated["verify_status"] == "CONFIRMED"
    assert updated["verify_label"] == "已确认"
    assert updated["verifier"] == "风控专员"
    assert updated["verified_at"] == "2026-08-13T10:00:00"

    alerts = store.fetch_alerts()
    assert len(alerts.rows) == 1
    alert = alerts.rows[0]
    assert alert[1] == "SENTIMENT"
    assert alert[5] == "CRITICAL"
    assert alert[9] == "2026-08-13T10:00:00"

    with duckdb.connect(str(tmp_path / "cases.duckdb")) as connection:
        notifications = connection.execute(
            "SELECT notification_id, notify_type, subject_id FROM notifications"
        ).fetchall()
    assert len(notifications) == 1
    assert notifications[0][1] == "SENTIMENT_VERIFIED"
    assert notifications[0][2] == "陈志远"


def test_verify_excluded_writes_notification_but_no_alert(tmp_path: Path) -> None:
    sim = _sim(_sentiment(verify_status="PENDING", severity="重大"))
    store = CaseStore(tmp_path / "cases.duckdb")

    updated = verify_sentiment(
        sim,
        "S2026-001",
        decision="EXCLUDED",
        verifier="风控专员",
        now="2026-08-13T10:00:00",
        store=store,
    )

    assert updated["verify_status"] == "EXCLUDED"
    assert store.fetch_alerts().rows == ()
    with duckdb.connect(str(tmp_path / "cases.duckdb")) as connection:
        count = connection.execute("SELECT COUNT(*) FROM notifications").fetchone()
    assert int(count[0]) == 1


def test_verify_requires_pending_and_known_id() -> None:
    sim = _sim(_sentiment(verify_status="CONFIRMED"))

    try:
        verify_sentiment(sim, "S2026-001", decision="EXCLUDED", verifier="x", now="now")
    except ValueError:
        pass
    else:
        raise AssertionError("已核验舆情再次核验应报错")

    try:
        verify_sentiment(sim, "NOPE", decision="CONFIRMED", verifier="x", now="now")
    except KeyError:
        pass
    else:
        raise AssertionError("未知舆情应报 KeyError（404）")


def test_verify_twice_rejects_second_with_value_error(tmp_path: Path) -> None:
    """同一舆情连续核验两次：第一次成功，第二次抛 ValueError（409）。"""

    sim = _sim(_sentiment(verify_status="PENDING", severity="重大"))
    store = CaseStore(tmp_path / "cases.duckdb")

    first = verify_sentiment(
        sim,
        "S2026-001",
        decision="CONFIRMED",
        verifier="风控A",
        now="2026-08-13T10:00:00",
        store=store,
    )
    assert first["verify_status"] == "CONFIRMED"

    # 第二次核验：持久化记录已存在 → ValueError（service 映射 409）
    try:
        verify_sentiment(
            sim,
            "S2026-001",
            decision="EXCLUDED",
            verifier="风控B",
            now="2026-08-13T11:00:00",
            store=store,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("重复核验应抛 ValueError（409）")

    # 留痕仍然只有一条（幂等写入）
    alerts = store.fetch_alerts()
    assert len(alerts.rows) == 1
    with duckdb.connect(str(tmp_path / "cases.duckdb")) as connection:
        notifications = connection.execute("SELECT COUNT(*) FROM notifications").fetchone()
    assert int(notifications[0]) == 1


# ---------------------------------------------------------------------------
# 项目事前评估
# ---------------------------------------------------------------------------


def test_pre_assessment_blacklist_intercepts(store: DuckDBStore) -> None:
    sim = _sim(new_projects=[_new_project(customer_list="黑名单", amount_wan=300.0)])

    result = run_pre_assessment(store, sim, "P2026-101")

    assert result["conclusion"] == "不建议通过"
    assert result["force_review"] is True
    assert any("黑名单" in str(reason) for reason in result["reasons"])


def test_pre_assessment_700_wan_forces_review(store: DuckDBStore) -> None:
    sim = _sim(new_projects=[_new_project(customer_list="一般", amount_wan=700.0)])

    result = run_pre_assessment(store, sim, "P2026-101")

    assert result["amount_tier"] == ">=700"
    assert result["force_review"] is True
    assert any("700 万元" in str(reason) for reason in result["reasons"])


def test_pre_assessment_normal_pass(store: DuckDBStore) -> None:
    # C001 在夹具中为一般客户（非黑名单），应收快照无超期 → 正常通过
    sim = _sim(
        new_projects=[_new_project(customer_list="一般", amount_wan=300.0, customer_id="C001")]
    )

    result = run_pre_assessment(store, sim, "P2026-101")

    assert result["conclusion"] == "正常通过"
    assert result["force_review"] is False
    assert result["amount_tier"] == "300~500"


def test_pre_assessment_unknown_project_raises(store: DuckDBStore) -> None:
    sim = _sim(new_projects=[_new_project()])

    try:
        run_pre_assessment(store, sim, "NOT_EXIST")
    except ValueError:
        pass
    else:
        raise AssertionError("未知项目应报错")


def test_list_new_projects_includes_tier_and_simulated_flag() -> None:
    sim = _sim(
        new_projects=[
            _new_project("P2026-101", amount_wan=180.0),
            _new_project("P2026-102", amount_wan=760.0),
        ]
    )

    rows = list_new_projects(sim)

    assert len(rows) == 2
    by_id = {str(row["project_id"]): row for row in rows}
    assert by_id["P2026-101"]["amount_tier"] == "<300"
    assert by_id["P2026-102"]["amount_tier"] == ">=700"
    assert all(row["simulated"] is True for row in rows)


def test_list_projects_marks_existing_as_not_simulated(store: DuckDBStore) -> None:
    """存量合同项目必须 simulated=False（真实性标记不混淆）。"""

    rows = list_projects(store, _sim())
    assert len(rows) >= 1
    assert all(row["simulated"] is False for row in rows)
    assert all(row["project_id"] for row in rows)


def test_amount_tier_boundaries() -> None:
    assert amount_tier(299.0) == "<300"
    assert amount_tier(300.0) == "300~500"
    assert amount_tier(499.0) == "300~500"
    assert amount_tier(500.0) == "500~700"
    assert amount_tier(699.0) == "500~700"
    assert amount_tier(700.0) == ">=700"
    assert amount_tier(920.0) == ">=700"

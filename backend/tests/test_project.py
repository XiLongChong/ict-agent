"""项目事前评估测试（模拟数据，不触碰真实业务库）。"""

from __future__ import annotations

from ict_agent.data import DuckDBStore
from ict_agent.project import amount_tier, list_new_projects, list_projects, run_pre_assessment
from ict_agent.simdata import SimulatedData, SimulatedNewProject


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


def _sim(new_projects: list[SimulatedNewProject] | None = None) -> SimulatedData:
    return SimulatedData(
        project_stages=(),
        guarantors=(),
        new_projects=tuple(new_projects or ()),
    )


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


def test_list_projects_includes_risk_metrics(store: DuckDBStore) -> None:
    """存量项目必须带真实风险指标与等级字段。"""

    rows = list_projects(store, _sim())
    assert len(rows) >= 1
    first = rows[0]
    for key in (
        "paid_amount_wan",
        "payment_rate",
        "overdue_rate",
        "margin_rate",
        "term_gap_days",
        "risk_level",
        "risk_note",
    ):
        assert key in first, f"存量项目缺少字段 {key}"
    assert first["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def test_risk_level_rules() -> None:
    """风险分级规则：负毛利/低回款升级。"""

    from ict_agent.project import _risk_level

    assert _risk_level(["负毛利"]) == "CRITICAL"
    assert _risk_level(["回款率低于 30%"]) == "CRITICAL"
    assert _risk_level(["应收超期率高于 50%"]) == "HIGH"
    assert _risk_level(["回款率低于 60%"]) == "HIGH"
    assert _risk_level(["账期超期 90 天"]) == "MEDIUM"
    assert _risk_level(["回款率低于 80%"]) == "MEDIUM"
    assert _risk_level([]) == "LOW"


def test_amount_tier_boundaries() -> None:
    assert amount_tier(299.0) == "<300"
    assert amount_tier(300.0) == "300~500"
    assert amount_tier(499.0) == "300~500"
    assert amount_tier(500.0) == "500~700"
    assert amount_tier(699.0) == "500~700"
    assert amount_tier(700.0) == ">=700"
    assert amount_tier(920.0) == ">=700"

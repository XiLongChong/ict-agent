"""健康度计算引擎测试（阶段 A）。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from ict_agent.data import DuckDBStore, rebuild_database
from ict_agent.health import (
    CONTRACT_HEALTH_WEIGHTS,
    HEALTH_WEIGHTS,
    compute_contract_health,
    compute_customer_health,
    compute_health_scores,
    grade_of,
)
from ict_agent.simdata import load_simulated_data

CUSTOMER_CREDIT = (
    "客户编号_中台,客户名称,授信额度,黑白名单状态,黑白名单原因,"
    "黑白名单创建时间,失信分级,净资产,净利润,信用保险\n"
    "C001,测试客户甲,1000,0,,2025-01-01,一般,3000,100,N\n"
    "C002,测试客户乙,2000,1,核心客户,2025-01-01,低,5000,300,Y\n"
    "C003,测试客户丙,0,2,黑名单,2025-01-01,高,100,10,N\n"
    "C004,测试客户丁,500,3,观察,2025-01-01,中,800,50,N\n"
    "C005,测试客户戊,300,0,,2025-01-01,一般,200,20,N\n"
)

AR_SNAPSHOTS = (
    "快照时间,合同号,客户编号,客户名称,销售订单号,应收金额,超期应收金额,"
    "超期30天以上金额,超期60天以上金额,最终承诺还款日期,是否展期,超期天数,物料编码\n"
    "2026-06-30,X1,C001,测试客户甲,S1,900,100,50,20,2026-06-15,N,30,M1\n"
    "2026-07-31,X1,C001,测试客户甲,S1,1000,100,50,20,2026-07-15,N,30,M1\n"
    "2026-06-30,X2,C002,测试客户乙,S2,800,0,0,0,2026-06-30,N,0,M1\n"
    "2026-07-31,X2,C002,测试客户乙,S2,500,0,0,0,2026-07-15,N,0,M1\n"
    "2026-07-31,X3,C003,测试客户丙,S3,1000,700,600,500,2026-07-01,Y,120,M1\n"
    "2026-07-31,X4,C004,测试客户丁,S4,2000,500,300,100,2026-07-20,N,45,M1\n"
)

SALES = (
    "出库日期,客户编号,客户名称,合同号,销售订单号,库存组织名称,物料编码,"
    "数量,出库类型,事务处理类型名称,销售金额_折扣后_含税,出库成本金额\n"
    "2026-07-01,C001,测试客户甲,X1,S1,W1,M1,1,销售出库,正常销售,1000,800\n"
    "2026-06-05,C001,测试客户甲,X1,S1,W1,M1,1,销售出库,正常销售,500,400\n"
    "2026-07-10,C002,测试客户乙,X2,S2,W1,M1,1,销售出库,正常销售,600,500\n"
    "2026-07-12,C003,测试客户丙,X3,S3,W1,M1,1,销售出库,正常销售,800,700\n"
    "2026-07-15,C004,测试客户丁,X4,S4,W1,M1,1,销售出库,正常销售,2000,1800\n"
)

PAYMENTS = (
    "回款日期,客户编号,合同号,销售订单号,回款金额,超期利息金额,最终承诺还款日期,"
    "是否超期,超期天数,回款账龄,物料编码\n"
    "2026-07-20,C001,X1,S1,900,0,2026-07-15,N,0,15,M1\n"
    "2026-07-25,C002,X2,S2,500,0,2026-07-20,N,0,10,M1\n"
    "2026-07-30,C004,X4,S4,1000,0,2026-07-25,N,0,20,M1\n"
)

CONTRACTS = (
    "申请日期,合同编号,合同状态,客户名称,销售金额,实估毛利率_不含税,"
    "实际净毛利率_不含税,合同文本账期,实际账期,开票金额1\n"
    "2026-03-01,C1,流程结束,测试客户乙,1000,0.2,0.15,60,60,800\n"
    "2026-04-01,C2,流程结束,测试客户丙,800,0.05,-0.1,60,90,600\n"
    "2026-06-01,C5,流程结束,测试客户戊,300,0.1,0.08,60,60,200\n"
)

INVENTORY = (
    "快照日期,物料编码,库存组织名称,数量,库龄,含税总价,是否超期,超期天数\n"
    "2026-06-30,M1,W1,1,10,100,N,0\n"
)

EXTENSIONS = (
    "快照时间,合同号,客户编号,销售订单号,物料编码,最终承诺还款日期,是否展期,"
    "超期天数,gkey\n"
    "2026-05-01,X3,C003,S3,M1,2026-07-01,Y,120,g1\n"
)

PROJECT_STAGES = (
    "合同编号,项目名称,客户名称,项目金额_万元,项目阶段,计划回款日期,"
    "里程碑进度_%,计划交付日期\n"
    "C1,项目P001,测试客户乙,800,执行,2026-09-30,60,2026-08-31\n"
    "C2,项目P002,测试客户丙,500,回款,2026-07-01,90,2026-06-30\n"
    "C3,项目P003,测试客户乙,300,结束,2026-07-31,100,2026-06-30\n"
    "C4,项目P004,测试客户丁,400,验收,2026-12-31,70,2026-11-30\n"
)

GUARANTORS = (
    "担保人ID,客户编号,客户名称,担保人名称,担保类型,担保金额_万元,担保人状态,"
    "关联合同或项目,备注\n"
    "G1,C002,测试客户乙,担保公司甲,公司,1000,正常,项目P001,存量项目担保\n"
    "G2,C003,测试客户丙,担保公司乙,公司,500,经营异常,项目P002,存量项目担保\n"
    "G3,C004,测试客户丁,个人担保丙,个人,200,待核验,项目P004,存量项目担保\n"
    "G4,C001,测试客户甲,担保公司丁,公司,800,正常,项目P005,存量项目担保\n"
)

SENTIMENTS = (
    "舆情编号,标题,来源,发布时间,涉及主体类型,涉及主体,事件类型,严重程度,"
    "影响金额_万元,真实性状态,关联合同或项目,处理状态\n"
    "S1,测试客户丙涉诉,法院公告,2026-07-01,客户,测试客户丙,诉讼,高,200,已确认,,已处理\n"
    "S2,测试客户丙资金链紧张,网络新闻,2026-07-10,客户,测试客户丙,负面新闻,中,0,待核验,,未处理\n"
    "S3,担保公司乙经营异常,工商公示,2026-06-20,担保人,担保公司乙,经营异常,高,0,已确认,项目P002,处理中\n"
    "S4,测试客户甲正常经营,行业媒体,2026-07-05,客户,测试客户甲,正面报道,低,0,已确认,,已处理\n"
    "S5,测试客户乙网传欠款已排除,网络新闻,2026-07-02,客户,测试客户乙,负面新闻,中,0,已排除,,已处理\n"
)

NEW_PROJECTS = (
    "项目编号,项目名称,客户编号,客户名称,客户名单,项目金额_万元,授信金额_万元,"
    "担保人,申请日期,计划回款日期,备注\n"
)


def _write_csv(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        handle.write(content)


@pytest.fixture
def store(tmp_path: Path) -> DuckDBStore:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    _write_csv(raw_dir / "客户授信.csv", CUSTOMER_CREDIT)
    _write_csv(raw_dir / "应收快照_月末24期.csv", AR_SNAPSHOTS)
    _write_csv(raw_dir / "销售流水.csv", SALES)
    _write_csv(raw_dir / "业务回款明细.csv", PAYMENTS)
    _write_csv(raw_dir / "增值合同签约明细.csv", CONTRACTS)
    _write_csv(raw_dir / "库龄快照_季末8期.csv", INVENTORY)
    _write_csv(raw_dir / "展期记录.csv", EXTENSIONS)
    database_path = tmp_path / "processed" / "test.duckdb"
    rebuild_database(raw_dir, database_path)
    return DuckDBStore(database_path)


@pytest.fixture
def sim(tmp_path: Path):
    simulated_dir = tmp_path / "simulated"
    simulated_dir.mkdir()
    _write_csv(simulated_dir / "sim_project_stages.csv", PROJECT_STAGES)
    _write_csv(simulated_dir / "sim_guarantors.csv", GUARANTORS)
    _write_csv(simulated_dir / "sim_sentiments.csv", SENTIMENTS)
    _write_csv(simulated_dir / "sim_new_projects.csv", NEW_PROJECTS)
    return load_simulated_data(simulated_dir)


def _customer_by_id(results: list[dict], customer_id: str) -> dict:
    for item in results:
        if item["subject_id"] == customer_id:
            return item
    raise AssertionError(f"缺少客户 {customer_id} 的健康度结果")


def _dimension(result: dict, key: str) -> dict:
    for dim in result["dimensions"]:
        if dim["key"] == key:
            return dim
    raise AssertionError(f"缺少维度 {key}")


# ---------------------------------------------------------------------------
# grade_of 边界
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (79, "WATCH"),
        (80, "HEALTHY"),
        (59, "WARNING"),
        (60, "WATCH"),
        (39, "HIGH_RISK"),
        (40, "WARNING"),
        (100, "HEALTHY"),
        (0, "HIGH_RISK"),
    ],
)
def test_grade_of_boundaries(score: float, expected: str) -> None:
    assert grade_of(score) == expected


# ---------------------------------------------------------------------------
# 权重
# ---------------------------------------------------------------------------


def test_health_weights_sum() -> None:
    assert sum(HEALTH_WEIGHTS.values()) == pytest.approx(100.0)
    assert sum(CONTRACT_HEALTH_WEIGHTS.values()) == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# 客户健康度：结构 / 缺失中性分 / 维度分 / 舆情排除 / 趋势
# ---------------------------------------------------------------------------


def test_customer_health_output_structure(store: DuckDBStore, sim) -> None:
    results = compute_customer_health(store, sim)
    assert len(results) == 5
    for item in results:
        assert item["subject_type"] == "CUSTOMER"
        assert item["subject_id"]
        assert item["subject_label"]
        assert isinstance(item["score"], float)
        assert item["grade"] in {"HEALTHY", "WATCH", "WARNING", "HIGH_RISK"}
        assert len(item["dimensions"]) == 6
        for dim in item["dimensions"]:
            assert set(dim) == {"key", "name", "score", "weight", "missing"}
            assert isinstance(dim["missing"], bool)
        assert set(item["drivers"]) == {"down", "up"}
        assert isinstance(item["drivers"]["down"], list)
        assert isinstance(item["drivers"]["up"], list)
        assert isinstance(item["trend"], list)
        assert item["computed_at"]


def test_customer_health_dimension_weights(store: DuckDBStore, sim) -> None:
    results = compute_customer_health(store, sim)
    for item in results:
        total_weight = sum(dim["weight"] for dim in item["dimensions"])
        assert total_weight == pytest.approx(100.0)


def test_missing_dimensions_neutral(store: DuckDBStore, sim) -> None:
    results = compute_customer_health(store, sim)
    customer = _customer_by_id(results, "C005")
    # C005 无应收、无销售/回款、无担保人、无舆情、无项目阶段
    for key in ("payment", "progress", "receivable", "guarantor", "sentiment"):
        dim = _dimension(customer, key)
        assert dim["missing"] is True
        assert dim["score"] == pytest.approx(60.0)
    # 授信维度仍可来自名单 + 合同毛利，不缺失
    assert _dimension(customer, "credit")["missing"] is False


def test_customer_dimension_scores(store: DuckDBStore, sim) -> None:
    results = compute_customer_health(store, sim)
    customer = _customer_by_id(results, "C003")
    # C003：黑名单 + 高应收超期率 + 无回款 + 担保人异常 + 已确认/待核验舆情
    assert _dimension(customer, "receivable")["score"] == pytest.approx(0.0)
    assert _dimension(customer, "payment")["score"] == pytest.approx(0.0)
    assert _dimension(customer, "credit")["score"] == pytest.approx(25.0)
    assert _dimension(customer, "guarantor")["score"] == pytest.approx(30.0)
    assert _dimension(customer, "sentiment")["score"] == pytest.approx(75.0)
    assert _dimension(customer, "progress")["score"] == pytest.approx(90.0)
    assert customer["grade"] == "HIGH_RISK"


def test_sentiment_excluded_not_counted(store: DuckDBStore, sim) -> None:
    results = compute_customer_health(store, sim)
    # C002 仅有一条已排除负面舆情，视为无舆情 → 中性 60 且缺失
    c002 = _customer_by_id(results, "C002")
    assert _dimension(c002, "sentiment")["missing"] is True
    assert _dimension(c002, "sentiment")["score"] == pytest.approx(60.0)
    # C005 无任何舆情事件 → 同样中性 60 且缺失（证明已排除事件不影响）
    c005 = _customer_by_id(results, "C005")
    assert _dimension(c005, "sentiment")["missing"] is True
    assert _dimension(c005, "sentiment")["score"] == pytest.approx(60.0)
    # C003 的已确认/待核验负面舆情确实降低分数
    c003 = _customer_by_id(results, "C003")
    assert _dimension(c003, "sentiment")["missing"] is False
    assert _dimension(c003, "sentiment")["score"] == pytest.approx(75.0)


def test_customer_trend_from_ar_snapshots(store: DuckDBStore, sim) -> None:
    results = compute_customer_health(store, sim)
    c001 = _customer_by_id(results, "C001")
    assert len(c001["trend"]) == 2
    # 按时间升序
    assert [point["period"] for point in c001["trend"]] == ["2026-06-30", "2026-07-31"]
    for point in c001["trend"]:
        assert set(point) == {"period", "score"}
        assert isinstance(point["score"], float)


def test_customer_drivers_present(store: DuckDBStore, sim) -> None:
    results = compute_customer_health(store, sim)
    c003 = _customer_by_id(results, "C003")
    joined = "，".join(c003["drivers"]["down"])
    assert "黑名单" in joined
    assert "担保人状态异常" in joined
    assert "已确认负面舆情" in joined


# ---------------------------------------------------------------------------
# 合同健康度
# ---------------------------------------------------------------------------


def test_contract_health(store: DuckDBStore, sim, monkeypatch) -> None:
    import ict_agent.health as health

    monkeypatch.setattr(health, "_today", lambda: date(2026, 8, 13))
    results = compute_contract_health(store, sim)
    assert len(results) == 4
    for item in results:
        assert item["subject_type"] == "CONTRACT"
        assert item["subject_id"]
        assert item["subject_label"]
        assert len(item["dimensions"]) == 5
        assert set(item["drivers"]) == {"down", "up"}
        assert item["trend"] == []

    by_id = {item["subject_id"]: item for item in results}

    # C1 项目P001：执行阶段、里程碑 60、计划回款 2026-09-30（48 天后）
    c1 = by_id["C1"]
    assert _dimension(c1, "progress")["score"] == pytest.approx(64.0)
    assert _dimension(c1, "payment")["score"] == pytest.approx(85.0)
    assert _dimension(c1, "contract")["score"] == pytest.approx(90.0)
    assert _dimension(c1, "guarantor")["score"] == pytest.approx(100.0)
    assert _dimension(c1, "sentiment")["missing"] is True
    assert c1["score"] == pytest.approx(76.35, abs=0.06)
    assert c1["grade"] == "WATCH"

    # C2 项目P002：回款阶段、里程碑 90、计划回款已逾期 43 天、担保人经营异常、项目负面舆情已确认
    c2 = by_id["C2"]
    assert _dimension(c2, "progress")["score"] == pytest.approx(45.0)
    assert _dimension(c2, "payment")["score"] == pytest.approx(10.0)
    assert _dimension(c2, "contract")["score"] == pytest.approx(40.0)
    assert _dimension(c2, "guarantor")["score"] == pytest.approx(30.0)
    assert _dimension(c2, "sentiment")["score"] == pytest.approx(80.0)
    assert c2["score"] == pytest.approx(37.5)
    assert c2["grade"] == "HIGH_RISK"


# ---------------------------------------------------------------------------
# 合并
# ---------------------------------------------------------------------------


def test_compute_health_scores_merges(store: DuckDBStore, sim) -> None:
    merged = compute_health_scores(store, sim)
    customer_count = len(compute_customer_health(store, sim))
    contract_count = len(compute_contract_health(store, sim))
    assert len(merged) == customer_count + contract_count
    assert {item["subject_type"] for item in merged} == {"CUSTOMER", "CONTRACT"}

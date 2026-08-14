"""健康度计算引擎测试（按业务类型分支）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from ict_agent.data import DuckDBStore, rebuild_database
from ict_agent.health import (
    CUSTOMER_SERVICE_CLOUD_WEIGHTS,
    _concentration_score,
    compute_contract_health,
    compute_customer_health,
    compute_health_scores,
    grade_of,
)

SALES = (
    "出库日期,客户编号,客户名称,合同号,销售订单号,库存组织名称,物料编码,"
    "数量,出库类型,事务处理类型名称,销售金额_折扣后_含税,出库成本金额,订单类型,核算大类名称\n"
    # 纯分销客户 C001：1-30 天超期
    "2026-07-01,C001,分销客户甲,X1,S1,W1,M1,1,销售出库,正常销售,1000,800,信产常规销售订单,IPHONE\n"
    # 纯项目客户 C002：未超期
    "2026-07-01,C002,项目客户乙,P001,S2,W1,M1,1,销售出库,正常销售,5000,4000,信产项目N,IT-存储\n"
    # 混合客户 C003：项目 3000 + 分销 1000
    "2026-07-01,C003,混合客户丙,P002,S3,W1,M1,1,销售出库,正常销售,3000,2400,信产项目S,IT-存储\n"
    "2026-07-01,C003,混合客户丙,X3,S4,W1,M1,1,销售出库,正常销售,1000,800,信息科技常规销售订单,IPHONE\n"
    # 无授信客户 C004：黑名单，61-120 天超期
    "2026-07-01,C004,无授信客户丁,X4,S5,W1,M1,1,销售出库,正常销售,800,640,信产常规销售订单,IPHONE\n"
    # 服务云客户 C005
    "2026-07-01,C005,服务云客户戊,SVC1,S6,W1,M1,1,销售出库,正常销售,2000,1200,云服务常规销售订单,微软Azure\n"
)

CUSTOMER_CREDIT = (
    "客户编号_中台,客户名称,授信额度,黑白名单状态,黑白名单原因,"
    "黑白名单创建时间,失信分级,净资产,净利润,信用保险\n"
    "C001,分销客户甲,1000,0,,2025-01-01,一般,3000,100,N\n"
    "C002,项目客户乙,2000,1,核心客户,2025-01-01,低,5000,300,Y\n"
    "C003,混合客户丙,1000,0,,2025-01-01,一般,3000,100,N\n"
    "C004,无授信客户丁,0,2,黑名单,2025-01-01,高,100,10,N\n"
    "C005,服务云客户戊,500,0,,2025-01-01,一般,2000,80,N\n"
)

PAYMENTS = (
    "回款日期,客户编号,合同号,销售订单号,回款金额,超期利息金额,"
    "最终承诺还款日期,是否超期,超期天数,物料编码\n"
    "2026-07-15,C001,X1,S1,800,0,2026-07-15,N,0,M1\n"
    "2026-07-20,C002,P001,S2,3000,0,2026-07-20,N,0,M1\n"
    "2026-07-18,C003,X3,S4,600,0,2026-07-18,N,0,M1\n"
)

CONTRACTS = (
    "申请日期,合同编号,合同状态,客户名称,项目名称,销售金额,实际净毛利率_不含税,"
    "合同文本账期,实际账期,开票金额1\n"
    "2026-05-01,P001,流程结束,项目客户乙,项目P001,5000,0.15,60,60,4000\n"
    # 项目名称非空但对应销售是分销，不能进入项目合同健康度
    "2026-05-01,X1,流程结束,分销客户甲,渠道合同X1,1000,0.10,30,30,1000\n"
)

AR_SNAPSHOTS = (
    "快照时间,合同号,客户编号,客户名称,销售订单号,应收金额,超期应收金额,"
    "超期30天以上金额,超期60天以上金额,最终承诺还款日期,是否展期,超期天数,物料编码\n"
    # C001 分销：应收 1000，超期 15 天（1-30 桶）
    "2026-07-31,X1,C001,分销客户甲,S1,1000,200,0,0,2026-07-15,N,15,M1\n"
    # C002 项目：应收 5000，未超期
    "2026-07-31,P001,C002,项目客户乙,S2,5000,0,0,0,2026-07-20,N,0,M1\n"
    # C003 混合：应收 4000，超期 90 天（61-120 桶）
    "2026-07-31,P002,C003,混合客户丙,S3,4000,3000,2000,1000,2026-05-01,Y,90,M1\n"
    # C004 无授信：应收 800，超期 200 天（>120 桶）
    "2026-07-31,X4,C004,无授信客户丁,S5,800,800,800,800,2026-01-01,Y,200,M1\n"
)

INVENTORY = (
    "快照日期,物料编码,库存组织名称,数量,库龄,含税总价,是否超期\n2026-06-30,M1,W1,5,10,500,N\n"
)

EXTENSIONS = (
    "快照时间,合同号,客户编号,销售订单号,物料编码,最终承诺还款日期,是否展期,超期天数,gkey\n"
    "2026-05-01,X1,C001,S1,M1,2026-06-30,Y,0,g1\n"
)


def _write_csv(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        handle.write(content)


@pytest.fixture
def store(tmp_path: Path) -> DuckDBStore:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    _write_csv(raw_dir / "销售流水.csv", SALES)
    _write_csv(raw_dir / "业务回款明细.csv", PAYMENTS)
    _write_csv(raw_dir / "增值合同签约明细.csv", CONTRACTS)
    _write_csv(raw_dir / "应收快照_月末24期.csv", AR_SNAPSHOTS)
    _write_csv(raw_dir / "库龄快照_季末8期.csv", INVENTORY)
    _write_csv(raw_dir / "展期记录.csv", EXTENSIONS)
    _write_csv(raw_dir / "客户授信.csv", CUSTOMER_CREDIT)
    database_path = tmp_path / "processed" / "test.duckdb"
    rebuild_database(raw_dir, database_path)
    return DuckDBStore(database_path)


def _customer_by_id(results: list[dict], customer_id: str) -> dict:
    for item in results:
        if item["subject_type"] == "CUSTOMER" and item["subject_id"] == customer_id:
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
# 客户健康度：结构 / 业务类型权重 / 维度分 / 缺数据
# ---------------------------------------------------------------------------


def test_customer_health_output_structure(store: DuckDBStore) -> None:
    results = compute_customer_health(store)

    assert len(results) == 5
    for item in results:
        assert item["subject_type"] == "CUSTOMER"
        assert item["subject_id"]
        assert item["subject_label"]
        assert item["business_type"] in ("PROJECT", "DISTRIBUTION", "SERVICE_CLOUD")
        assert isinstance(item["score"], float)
        assert item["grade"] in {"HEALTHY", "WATCH", "WARNING", "HIGH_RISK"}
        assert len(item["dimensions"]) == 5
        for dim in item["dimensions"]:
            assert set(dim) == {"key", "name", "score", "weight", "missing"}
            assert isinstance(dim["missing"], bool)
        assert set(item["drivers"]) == {"down", "up"}
        assert isinstance(item["trend"], list)
        assert item["computed_at"]


def test_business_type_drives_weights(store: DuckDBStore) -> None:
    results = compute_customer_health(store)

    # 纯分销 C001：逾期维度权重 = 25
    assert _dimension(_customer_by_id(results, "C001"), "overdue")["weight"] == pytest.approx(25.0)
    # 纯项目 C002：逾期维度权重 = 30
    assert _dimension(_customer_by_id(results, "C002"), "overdue")["weight"] == pytest.approx(30.0)
    # 混合 C003：项目 3000 / (3000+1000) = 0.75 → overdue 权重 = 30*0.75 + 25*0.25
    assert _dimension(_customer_by_id(results, "C003"), "overdue")["weight"] == pytest.approx(28.75)
    # 服务云 C005 使用独立权重，不复用分销权重
    service = _customer_by_id(results, "C005")
    assert service["business_type"] == "SERVICE_CLOUD"
    assert _dimension(service, "payment")["weight"] == pytest.approx(
        CUSTOMER_SERVICE_CLOUD_WEIGHTS["payment"]
    )


def test_aging_bucket_scores(store: DuckDBStore) -> None:
    results = compute_customer_health(store)

    # C001 全部应收在 1-30 天桶 → 超期维度 70
    assert _dimension(_customer_by_id(results, "C001"), "overdue")["score"] == pytest.approx(70.0)
    # C002 未超期 → 超期维度 100
    assert _dimension(_customer_by_id(results, "C002"), "overdue")["score"] == pytest.approx(100.0)
    # C003 全部在 61-120 天桶 → 超期维度 15
    assert _dimension(_customer_by_id(results, "C003"), "overdue")["score"] == pytest.approx(15.0)
    # C004 全部 >120 天 → 超期维度 0
    assert _dimension(_customer_by_id(results, "C004"), "overdue")["score"] == pytest.approx(0.0)


def test_missing_credit_and_blacklist(store: DuckDBStore) -> None:
    results = compute_customer_health(store)

    # C004 授信额度为 0 → 授信占用维度缺失
    credit_dim = _dimension(_customer_by_id(results, "C004"), "credit")
    assert credit_dim["missing"] is True
    # C004 黑名单 → 名单资质 10 分
    assert _dimension(_customer_by_id(results, "C004"), "list")["score"] == pytest.approx(10.0)
    # C002 白名单 → 名单资质 100 分
    assert _dimension(_customer_by_id(results, "C002"), "list")["score"] == pytest.approx(100.0)


def test_blacklist_customer_high_risk(store: DuckDBStore) -> None:
    results = compute_customer_health(store)

    # C004 黑名单 + 超期 200 天 + 无授信，应判高危
    assert _customer_by_id(results, "C004")["grade"] == "HIGH_RISK"


# ---------------------------------------------------------------------------
# 合同（项目）健康度
# ---------------------------------------------------------------------------


def test_contract_health(store: DuckDBStore) -> None:
    results = compute_contract_health(store)

    assert len(results) == 1
    item = results[0]
    assert item["subject_type"] == "CONTRACT"
    assert item["subject_id"] == "P001"
    assert item["subject_label"] == "项目P001"
    assert item["business_type"] == "PROJECT"
    assert len(item["dimensions"]) == 5
    assert set(item["drivers"]) == {"down", "up"}
    assert item["trend"] == []

    # P001 未超期 → 超期维度 100
    assert _dimension(item, "overdue")["score"] == pytest.approx(100.0)
    # 毛利 0.15 → 60 + 0.15*200 = 90
    assert _dimension(item, "margin")["score"] == pytest.approx(90.0)
    # 账期偏差 0 → 100
    assert _dimension(item, "term_gap")["score"] == pytest.approx(100.0)
    # 回款 3000 / 5000 = 0.6 → 0.6/0.9*100 ≈ 66.7
    assert _dimension(item, "payment")["score"] == pytest.approx(66.7, abs=0.1)


def test_named_non_project_contract_is_excluded(store: DuckDBStore) -> None:
    """项目合同入口必须复用订单业务分类，不能只看合同项目名称。"""

    assert {item["subject_id"] for item in compute_contract_health(store)} == {"P001"}


def test_contract_concentration_requires_contract_receivable() -> None:
    """客户有应收但当前合同无应收时，不能把集中度误记为健康满分。"""

    score, missing = _concentration_score(0.0, 1000.0)
    assert score == pytest.approx(60.0)
    assert missing is True


# ---------------------------------------------------------------------------
# 合并
# ---------------------------------------------------------------------------


def test_compute_health_scores_merges(store: DuckDBStore) -> None:
    merged = compute_health_scores(store)
    customer_count = len(compute_customer_health(store))
    contract_count = len(compute_contract_health(store))

    assert len(merged) == customer_count + contract_count
    assert {item["subject_type"] for item in merged} == {"CUSTOMER", "CONTRACT"}

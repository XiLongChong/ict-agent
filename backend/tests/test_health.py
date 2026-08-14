"""按“公司 × 业务类型”分轨的健康度测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from ict_agent.data import DuckDBStore, rebuild_database
from ict_agent.health import compute_health_scores, grade_of

SALES = (
    "出库日期,客户编号,客户名称,合同号,销售订单号,库存组织名称,物料编码,"
    "数量,出库类型,事务处理类型名称,销售金额_折扣后_含税,出库成本金额,订单类型,核算大类名称\n"
    "2026-07-01,C001,分销客户甲,X1,S1,W1,M1,1,销售出库,正常销售,1000,800,信产常规销售订单,IPHONE\n"
    "2026-07-01,C002,项目客户乙,P001,S2,W1,M1,1,销售出库,正常销售,5000,4000,信产项目N,IT-存储\n"
    "2026-07-01,C003,混合客户丙,P002,S3,W1,M1,1,销售出库,正常销售,3000,2400,信产项目S,IT-存储\n"
    "2026-07-01,C003,混合客户丙,X3,S4,W1,M1,1,销售出库,正常销售,1000,800,信息科技常规销售订单,IPHONE\n"
    "2026-07-01,C004,无授信客户丁,X4,S5,W1,M1,1,销售出库,正常销售,800,640,信产常规销售订单,IPHONE\n"
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
    "2026-05-01,X1,流程结束,分销客户甲,渠道合同X1,1000,-0.50,30,120,1000\n"
)

AR_SNAPSHOTS = (
    "快照时间,合同号,客户编号,客户名称,销售订单号,应收金额,超期应收金额,"
    "超期30天以上金额,超期60天以上金额,最终承诺还款日期,是否展期,超期天数,物料编码\n"
    "2026-07-31,X1,C001,分销客户甲,S1,1000,200,0,0,2026-07-15,N,15,M1\n"
    "2026-07-31,P001,C002,项目客户乙,S2,5000,0,0,0,2026-07-20,N,0,M1\n"
    "2026-07-31,P002,C003,混合客户丙,S3,4000,3000,2000,1000,2026-05-01,Y,90,M1\n"
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
    for filename, content in {
        "销售流水.csv": SALES,
        "业务回款明细.csv": PAYMENTS,
        "增值合同签约明细.csv": CONTRACTS,
        "应收快照_月末24期.csv": AR_SNAPSHOTS,
        "库龄快照_季末8期.csv": INVENTORY,
        "展期记录.csv": EXTENSIONS,
        "客户授信.csv": CUSTOMER_CREDIT,
    }.items():
        _write_csv(raw_dir / filename, content)
    database_path = tmp_path / "processed" / "test.duckdb"
    rebuild_database(raw_dir, database_path)
    return DuckDBStore(database_path)


def _segment(results: list[dict], customer_id: str, business_type: str) -> dict:
    for item in results:
        if item["subject_id"] == customer_id and item["business_type"] == business_type:
            return item
    raise AssertionError(f"缺少 {customer_id} / {business_type}")


def _dimension(item: dict, key: str) -> dict:
    return next(dimension for dimension in item["dimensions"] if dimension["key"] == key)


@pytest.mark.parametrize(
    ("score", "expected"),
    [(80, "HEALTHY"), (60, "WATCH"), (40, "WARNING"), (39, "HIGH_RISK")],
)
def test_grade_of_boundaries(score: float, expected: str) -> None:
    assert grade_of(score) == expected


def test_one_record_per_customer_business_type(store: DuckDBStore) -> None:
    results = compute_health_scores(store)

    assert len(results) == 6
    assert len({(item["subject_id"], item["business_type"]) for item in results}) == 6
    assert "subject_type" not in results[0]
    assert {(item["subject_id"], item["business_type"]) for item in results} >= {
        ("C003", "DISTRIBUTION"),
        ("C003", "PROJECT"),
    }


def test_each_business_type_uses_distinct_dimensions(store: DuckDBStore) -> None:
    results = compute_health_scores(store)

    distribution = _segment(results, "C001", "DISTRIBUTION")
    project = _segment(results, "C002", "PROJECT")
    service = _segment(results, "C005", "SERVICE_CLOUD")
    assert {d["key"] for d in distribution["dimensions"]} == {
        "payment",
        "overdue",
        "credit",
        "list",
        "activity",
    }
    assert {d["key"] for d in project["dimensions"]} == {
        "payment",
        "overdue",
        "margin",
        "term_gap",
        "list",
    }
    assert {d["key"] for d in service["dimensions"]} == {
        "payment",
        "overdue",
        "credit",
        "continuity",
        "list",
    }


def test_metrics_are_scoped_to_business_type(store: DuckDBStore) -> None:
    results = compute_health_scores(store)

    # C003 的应收属于项目订单，不能落入同公司的分销结果。
    assert _dimension(_segment(results, "C003", "PROJECT"), "overdue")["missing"] is False
    assert _dimension(_segment(results, "C003", "DISTRIBUTION"), "overdue")["missing"] is True


def test_project_uses_only_project_contracts(store: DuckDBStore) -> None:
    project = _segment(compute_health_scores(store), "C002", "PROJECT")

    assert _dimension(project, "margin")["score"] == pytest.approx(90.0)
    assert _dimension(project, "term_gap")["score"] == pytest.approx(100.0)


def test_service_cloud_uses_continuity_proxy(store: DuckDBStore) -> None:
    service = _segment(compute_health_scores(store), "C005", "SERVICE_CLOUD")

    assert _dimension(service, "continuity")["score"] == pytest.approx(16.7)
    assert "近 6 月服务云交易仅覆盖 1 个月" in service["drivers"]["down"]

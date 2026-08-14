"""业务类型判定测试（项目 / 分销 / 软件服务云）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from ict_agent.business_type import customer_business_profiles, order_business_type
from ict_agent.data import DuckDBStore, rebuild_database

SALES = (
    "出库日期,客户编号,客户名称,合同号,销售订单号,库存组织名称,物料编码,"
    "数量,出库类型,事务处理类型名称,销售金额_折扣后_含税,出库成本金额,订单类型,核算大类名称\n"
    # 纯分销客户 C001
    "2026-07-01,C001,分销客户甲,X1,S1,W1,M1,1,销售出库,正常销售,1000,800,信产常规销售订单,IPHONE\n"
    # 纯项目客户 C002
    "2026-07-01,C002,项目客户乙,X2,S2,W1,M1,1,销售出库,正常销售,5000,4000,信产项目N,IT-存储\n"
    # 纯服务云客户 C003
    "2026-07-01,C003,服务云客户丙,X3,S3,W1,M1,1,销售出库,正常销售,2000,1600,信息产品整机销售订单,微软Azure\n"
    # 混合客户 C004：项目金额大 → 主导 PROJECT
    "2026-07-01,C004,混合客户丁,X4,S4,W1,M1,1,销售出库,正常销售,3000,2400,信产项目S,IT-存储\n"
    "2026-07-01,C004,混合客户丁,X5,S5,W1,M1,1,销售出库,正常销售,1000,800,信产常规销售订单,IPHONE\n"
    # 混合客户 C005：分销金额大 → 主导 DISTRIBUTION，且含服务云
    "2026-07-01,C005,混合客户戊,X6,S6,W1,M1,1,销售出库,正常销售,5000,4000,信产常规销售订单,IPHONE\n"
    "2026-07-01,C005,混合客户戊,X7,S7,W1,M1,1,销售出库,正常销售,200,160,信息产品整机销售订单,微软Azure\n"
    # 负分销客户 C006：分销净额为负 → 兜底 DISTRIBUTION
    "2026-07-01,C006,负分销客户己,X8,S8,W1,M1,1,销售出库,正常销售,-5000,-4000,信产常规销售订单,IPHONE\n"
    # 类型与核算大类都为空时仍应兜底分销
    "2026-07-01,C008,空类型客户辛,X9,S9,W1,M1,1,销售出库,正常销售,100,80,,\n"
)

CUSTOMER_CREDIT = (
    "客户编号_中台,客户名称,授信额度,黑白名单状态,黑白名单原因,"
    "黑白名单创建时间,失信分级,净资产,净利润,信用保险\n"
    "C001,分销客户甲,1000,0,,2025-01-01,一般,3000,100,N\n"
    "C002,项目客户乙,1000,0,,2025-01-01,一般,3000,100,N\n"
    "C003,服务云客户丙,1000,0,,2025-01-01,一般,3000,100,N\n"
    "C004,混合客户丁,1000,0,,2025-01-01,一般,3000,100,N\n"
    "C005,混合客户戊,1000,0,,2025-01-01,一般,3000,100,N\n"
    "C006,负分销客户己,1000,0,,2025-01-01,一般,3000,100,N\n"
    "C007,无销售客户庚,1000,0,,2025-01-01,一般,3000,100,N\n"
    "C008,空类型客户辛,1000,0,,2025-01-01,一般,3000,100,N\n"
)

PAYMENTS = (
    "回款日期,客户编号,合同号,销售订单号,回款金额,超期利息金额,"
    "最终承诺还款日期,是否超期,超期天数,物料编码\n"
    "2026-07-15,C001,X1,S1,800,0,2026-07-15,N,0,M1\n"
)

CONTRACTS = (
    "申请日期,合同编号,合同状态,销售金额,实际净毛利率_不含税,开票金额1\n"
    "2026-05-01,X1,流程结束,1000,0.1,800\n"
)

AR_SNAPSHOTS = (
    "快照时间,合同号,客户编号,客户名称,销售订单号,应收金额,超期应收金额,"
    "超期30天以上金额,超期60天以上金额,最终承诺还款日期,是否展期,超期天数,物料编码\n"
    "2026-07-31,X1,C001,分销客户甲,S1,900,100,50,20,2026-07-15,N,30,M1\n"
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


def test_order_business_type_classifies() -> None:
    assert order_business_type("信产项目N", "IT-存储") == "PROJECT"
    assert order_business_type("信产项目S", "计算-计算") == "PROJECT"
    assert order_business_type("信产常规销售订单", "IPHONE") == "DISTRIBUTION"
    assert order_business_type("哆啦有货常规销售订单", "雷蛇") == "DISTRIBUTION"
    # 服务/云核算大类 → SERVICE_CLOUD（订单类型不是项目即可）
    assert order_business_type("信息产品整机销售订单", "微软Azure") == "SERVICE_CLOUD"
    assert order_business_type("信息产品整机销售订单", "西门子工业软件") == "SERVICE_CLOUD"
    assert order_business_type("信息产品整机销售订单", "长虹专业服务") == "SERVICE_CLOUD"
    # 项目订单优先于服务云核算大类
    assert order_business_type("信产项目N", "微软Azure") == "PROJECT"


def test_customer_business_profiles_pure_types(store: DuckDBStore) -> None:
    profiles = customer_business_profiles(store)

    assert profiles["C001"]["business_type"] == "DISTRIBUTION"
    assert profiles["C001"]["is_mixed"] is False
    assert profiles["C001"]["distribution_amount"] == pytest.approx(1000.0)

    assert profiles["C002"]["business_type"] == "PROJECT"
    assert profiles["C002"]["is_mixed"] is False
    assert profiles["C002"]["project_amount"] == pytest.approx(5000.0)

    assert profiles["C003"]["business_type"] == "SERVICE_CLOUD"
    assert profiles["C003"]["is_mixed"] is False
    assert profiles["C003"]["service_cloud_amount"] == pytest.approx(2000.0)


def test_mixed_customer_dominant_by_amount(store: DuckDBStore) -> None:
    profiles = customer_business_profiles(store)

    # C004：项目 3000 > 分销 1000 → 主导 PROJECT，标记混合
    c004 = profiles["C004"]
    assert c004["business_type"] == "PROJECT"
    assert c004["is_mixed"] is True
    assert c004["project_amount"] == pytest.approx(3000.0)
    assert c004["distribution_amount"] == pytest.approx(1000.0)

    # C005：分销 5000 > 服务云 200 → 主导 DISTRIBUTION，标记混合
    c005 = profiles["C005"]
    assert c005["business_type"] == "DISTRIBUTION"
    assert c005["is_mixed"] is True
    assert c005["distribution_amount"] == pytest.approx(5000.0)
    assert c005["service_cloud_amount"] == pytest.approx(200.0)


def test_negative_distribution_amount_falls_back(store: DuckDBStore) -> None:
    profiles = customer_business_profiles(store)

    # C006 无项目/服务云订单，分销净额为负 → 兜底 DISTRIBUTION，不误判
    assert profiles["C006"]["business_type"] == "DISTRIBUTION"
    assert profiles["C006"]["is_mixed"] is False


def test_customer_without_sales_excluded(store: DuckDBStore) -> None:
    profiles = customer_business_profiles(store)

    assert "C007" not in profiles
    assert set(profiles) == {"C001", "C002", "C003", "C004", "C005", "C006", "C008"}


def test_missing_order_type_and_category_fall_back_to_distribution(
    store: DuckDBStore,
) -> None:
    profile = customer_business_profiles(store)["C008"]

    assert profile["business_type"] == "DISTRIBUTION"
    assert profile["distribution_amount"] == pytest.approx(100.0)
    assert profile["distribution_order_count"] == 1

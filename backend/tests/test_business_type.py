"""业务类型判定测试（项目 / 分销）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from ict_agent.business_type import customer_business_types, order_type_business
from ict_agent.data import DuckDBStore, rebuild_database

SALES = (
    "出库日期,客户编号,客户名称,合同号,销售订单号,库存组织名称,物料编码,"
    "数量,出库类型,事务处理类型名称,销售金额_折扣后_含税,出库成本金额,订单类型\n"
    # 纯分销客户 C001
    "2026-07-01,C001,分销客户甲,X1,S1,W1,M1,1,销售出库,正常销售,1000,800,信产常规销售订单\n"
    # 纯项目客户 C002
    "2026-07-01,C002,项目客户乙,X2,S2,W1,M1,1,销售出库,正常销售,5000,4000,信产项目N\n"
    # 混合客户 C003：项目金额大 → PROJECT
    "2026-07-01,C003,混合客户丙,X3,S3,W1,M1,1,销售出库,正常销售,3000,2400,信产项目S\n"
    "2026-07-01,C003,混合客户丙,X4,S4,W1,M1,1,销售出库,正常销售,1000,800,信息科技常规销售订单\n"
    # 混合客户 C004：分销金额大 → DISTRIBUTION
    "2026-07-01,C004,混合客户丁,X5,S5,W1,M1,1,销售出库,正常销售,200,160,信产项目N\n"
    "2026-07-01,C004,混合客户丁,X6,S6,W1,M1,1,销售出库,正常销售,1000,800,哆啦有货常规销售订单\n"
    # 边界客户 C006：纯分销但净额为负（退货冲销）→ 应判 DISTRIBUTION 而非 PROJECT
    "2026-07-01,C006,负分销客户己,X7,S7,W1,M1,1,销售出库,正常销售,-5000,-4000,信产常规销售订单\n"
)

CUSTOMER_CREDIT = (
    "客户编号_中台,客户名称,授信额度,黑白名单状态,黑白名单原因,"
    "黑白名单创建时间,失信分级,净资产,净利润,信用保险\n"
    "C001,分销客户甲,1000,0,,2025-01-01,一般,3000,100,N\n"
    "C002,项目客户乙,1000,0,,2025-01-01,一般,3000,100,N\n"
    "C003,混合客户丙,1000,0,,2025-01-01,一般,3000,100,N\n"
    "C004,混合客户丁,1000,0,,2025-01-01,一般,3000,100,N\n"
    "C005,无销售客户戊,1000,0,,2025-01-01,一般,3000,100,N\n"
    "C006,负分销客户己,1000,0,,2025-01-01,一般,3000,100,N\n"
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


def test_order_type_business_classifies() -> None:
    assert order_type_business("信产项目N") == "PROJECT"
    assert order_type_business("信产项目S") == "PROJECT"
    assert order_type_business("信产常规销售订单") == "DISTRIBUTION"
    assert order_type_business("哆啦有货常规销售订单") == "DISTRIBUTION"
    assert order_type_business("信息服务常规销售订单") == "DISTRIBUTION"
    assert order_type_business("云服务常规销售订单") == "DISTRIBUTION"


def test_customer_business_types_amount_dominant(store: DuckDBStore) -> None:
    result = customer_business_types(store)

    assert result["C001"] == "DISTRIBUTION"  # 纯分销
    assert result["C002"] == "PROJECT"  # 纯项目
    assert result["C003"] == "PROJECT"  # 项目 3000 > 分销 1000
    assert result["C004"] == "DISTRIBUTION"  # 分销 1000 > 项目 200


def test_negative_distribution_amount_not_project(store: DuckDBStore) -> None:
    result = customer_business_types(store)

    # C006 无项目订单，分销净额为负（退货冲销）→ 不能因 0 > 负数 误判为项目类
    assert result["C006"] == "DISTRIBUTION"


def test_customer_without_sales_excluded(store: DuckDBStore) -> None:
    result = customer_business_types(store)

    assert "C005" not in result
    assert set(result) == {"C001", "C002", "C003", "C004", "C006"}

"""业务类型判定（项目 / 分销）。

从 `sales.订单类型` 推导授信客户的业务类型，供健康度等模块按业务类型选择不同的
算法口径。全部由真实数据确定性推导，不落库、不调用模型。

口径（与 docs/metric-contract.md 冻结一致）：
- 项目类订单 = 信产项目N / 信产项目S（企业级项目：数据中心/算力/数通/能源，一单一议）
- 分销类订单 = 其余全部订单类型（消费电子分销：手机/电脑/配件，走量账期滚动）
- 客户按金额主导归类：项目订单金额 > 分销订单金额 → PROJECT，否则 DISTRIBUTION
- 服务/云/智能 等尾类订单，货物构成仍为分销类（消费产品/设备），归入分销
"""

from __future__ import annotations

from typing import Literal

from ict_agent.data import DuckDBStore

BusinessType = Literal["PROJECT", "DISTRIBUTION"]

# 项目类订单类型（企业级项目，一单一议）；其余订单类型均为分销类
_PROJECT_ORDER_TYPES: frozenset[str] = frozenset({"信产项目N", "信产项目S"})
# 编译期常量内联进 SQL（非用户输入，无注入风险）
_PROJECT_ORDER_LIST = ", ".join(f"'{name}'" for name in sorted(_PROJECT_ORDER_TYPES))


def order_type_business(order_type: str) -> BusinessType:
    """单个订单类型 → 业务类型（PROJECT / DISTRIBUTION）。"""

    return "PROJECT" if order_type in _PROJECT_ORDER_TYPES else "DISTRIBUTION"


def customer_business_types(store: DuckDBStore) -> dict[str, BusinessType]:
    """全部授信客户的业务类型映射（金额主导）；无销售记录的客户不包含。"""

    result = store.fetch(
        f"""
        SELECT s."客户编号",
               COALESCE(SUM(CASE WHEN s."订单类型" IN ({_PROJECT_ORDER_LIST})
                                 THEN s."销售金额_折扣后_含税" END), 0) AS project_amount,
               COALESCE(SUM(CASE WHEN s."订单类型" NOT IN ({_PROJECT_ORDER_LIST})
                                 THEN s."销售金额_折扣后_含税" END), 0) AS distribution_amount
        FROM sales s
        JOIN customer_credit c ON s."客户编号" = c."客户编号_中台"
        GROUP BY 1
        """
    )
    business: dict[str, BusinessType] = {}
    for row in result.rows:
        project_amount = float(row[1] or 0.0)
        distribution_amount = float(row[2] or 0.0)
        business[str(row[0])] = (
            "PROJECT" if project_amount > distribution_amount else "DISTRIBUTION"
        )
    return business


__all__ = [
    "BusinessType",
    "customer_business_types",
    "order_type_business",
]

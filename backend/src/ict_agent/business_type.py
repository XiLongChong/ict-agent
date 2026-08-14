"""业务类型判定（项目 / 分销 / 软件服务云）。

业务类型是「交易级」属性——同一个客户可能既有项目订单又有分销订单，因此
客户级不强行二选一，而是返回三类金额分量、主导类型与是否混合标志，供健康度等
下游按金额加权。

判据（sales 表，确定性推导，不落库、不调用模型）：
- PROJECT 项目类：订单类型 ∈ {信产项目N, 信产项目S}（企业级项目，一单一议）
- SERVICE_CLOUD 软件服务云：核算大类为软件/云/服务（订阅与服务交付，非实体货物）
- DISTRIBUTION 分销类：其余全部（消费电子与走量企业级产品分销）

优先级：PROJECT（订单类型）> SERVICE_CLOUD（核算大类）> DISTRIBUTION（兜底）。
"""

from __future__ import annotations

from typing import Literal

from ict_agent.data import DuckDBStore

BusinessType = Literal["PROJECT", "DISTRIBUTION", "SERVICE_CLOUD"]

# 项目类订单类型（企业级项目，一单一议）
_PROJECT_ORDER_TYPES: frozenset[str] = frozenset({"信产项目N", "信产项目S"})
_PROJECT_ORDER_LIST = ", ".join(f"'{name}'" for name in sorted(_PROJECT_ORDER_TYPES))

# 软件/云/服务核算大类：订阅与服务交付，非实体货物
_SERVICE_CLOUD_CATEGORY_KEYWORDS: tuple[str, ...] = ("软件", "服务")
_SERVICE_CLOUD_CATEGORY_NAMES: frozenset[str] = frozenset(
    {
        "微软Azure",
        "微软365",
        "腾讯云",
        "华为云Stack-华为云Stack",
        "企业系统AliCloud",
        "ORACLE",
        "esight公共-esight公共",
    }
)
_SERVICE_CLOUD_NAMES_LIST = ", ".join(f"'{name}'" for name in sorted(_SERVICE_CLOUD_CATEGORY_NAMES))


def _is_service_cloud_category(category: str) -> bool:
    """核算大类是否属于软件/云/服务。"""

    if category in _SERVICE_CLOUD_CATEGORY_NAMES:
        return True
    return any(keyword in category for keyword in _SERVICE_CLOUD_CATEGORY_KEYWORDS)


def order_business_type(order_type: str, category: str = "") -> BusinessType:
    """单个订单 → 业务类型（PROJECT / DISTRIBUTION / SERVICE_CLOUD）。"""

    if order_type in _PROJECT_ORDER_TYPES:
        return "PROJECT"
    if _is_service_cloud_category(category):
        return "SERVICE_CLOUD"
    return "DISTRIBUTION"


def _dominant_business_type(
    project_amount: float,
    distribution_amount: float,
    service_cloud_amount: float,
) -> BusinessType:
    """金额主导类型：取正金额最大者；全非正时兜底 DISTRIBUTION。"""

    if (
        project_amount > 0
        and project_amount >= distribution_amount
        and project_amount >= service_cloud_amount
    ):
        return "PROJECT"
    if (
        service_cloud_amount > 0
        and service_cloud_amount >= distribution_amount
        and service_cloud_amount >= project_amount
    ):
        return "SERVICE_CLOUD"
    return "DISTRIBUTION"


def customer_business_profiles(store: DuckDBStore) -> dict[str, dict[str, object]]:
    """每个授信客户 → 业务画像（三类金额分量 + 主导类型 + 是否混合）。

    无销售记录的客户不包含。金额单位：元。三类金额分量互斥，合计等于客户
    销售净额；核算大类缺失或为空的订单归入分销。
    """

    result = store.fetch(
        f"""
        SELECT s."客户编号",
               SUM(CASE WHEN s."订单类型" IN ({_PROJECT_ORDER_LIST})
                        THEN s."销售金额_折扣后_含税" ELSE 0 END) AS project_amount,
               SUM(CASE WHEN s."订单类型" NOT IN ({_PROJECT_ORDER_LIST})
                          AND (s."核算大类名称" IN ({_SERVICE_CLOUD_NAMES_LIST})
                               OR s."核算大类名称" LIKE '%软件%'
                               OR s."核算大类名称" LIKE '%服务%')
                        THEN s."销售金额_折扣后_含税" ELSE 0 END) AS service_cloud_amount,
               SUM(CASE
                     WHEN s."订单类型" IN ({_PROJECT_ORDER_LIST}) THEN 0
                     WHEN s."核算大类名称" IN ({_SERVICE_CLOUD_NAMES_LIST})
                       OR s."核算大类名称" LIKE '%软件%'
                       OR s."核算大类名称" LIKE '%服务%' THEN 0
                     ELSE s."销售金额_折扣后_含税"
                   END) AS distribution_amount
        FROM sales s
        JOIN customer_credit c ON s."客户编号" = c."客户编号_中台"
        GROUP BY 1
        """
    )
    profiles: dict[str, dict[str, object]] = {}
    for row in result.rows:
        project_amount = float(row[1] or 0.0)
        service_cloud_amount = float(row[2] or 0.0)
        distribution_amount = float(row[3] or 0.0)
        if project_amount == 0.0 and service_cloud_amount == 0.0 and distribution_amount == 0.0:
            continue
        positive_count = sum(
            1
            for amount in (project_amount, distribution_amount, service_cloud_amount)
            if amount > 0
        )
        profiles[str(row[0])] = {
            "customer_id": str(row[0]),
            "project_amount": project_amount,
            "distribution_amount": distribution_amount,
            "service_cloud_amount": service_cloud_amount,
            "business_type": _dominant_business_type(
                project_amount, distribution_amount, service_cloud_amount
            ),
            "is_mixed": positive_count >= 2,
        }
    return profiles


__all__ = [
    "BusinessType",
    "customer_business_profiles",
    "order_business_type",
]

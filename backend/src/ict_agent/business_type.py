"""业务类型判定（项目 / 分销 / 软件服务云）。

业务类型是「交易级」属性——同一个客户可能既有项目订单又有分销订单，因此
案件和取证按实际涉及的业务类型分别展示，不给客户强行指定唯一主导类型。

判据（sales 表，确定性推导，不落库、不调用模型）：
- PROJECT 项目类：订单类型 ∈ {信产项目N, 信产项目S}（企业级项目，一单一议）
- SERVICE_CLOUD 软件服务云：核算大类为软件/云/服务（订阅与服务交付，非实体货物）
- DISTRIBUTION 分销类：其余全部（消费电子与走量企业级产品分销）

优先级：PROJECT（订单类型）> SERVICE_CLOUD（核算大类）> DISTRIBUTION（兜底）。
"""

from __future__ import annotations

from ict_agent.models import BusinessType

# 项目类订单类型（企业级项目，一单一议）
PROJECT_ORDER_TYPES: tuple[str, ...] = ("信产项目N", "信产项目S")
_PROJECT_ORDER_LIST = ", ".join(f"'{name}'" for name in PROJECT_ORDER_TYPES)

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

    if order_type in PROJECT_ORDER_TYPES:
        return "PROJECT"
    if _is_service_cloud_category(category):
        return "SERVICE_CLOUD"
    return "DISTRIBUTION"


def business_type_condition(alias: str, business_type: BusinessType) -> str:
    """返回与单笔判定一致的静态 SQL 条件。alias 只能由内部查询传入。"""

    project = f"COALESCE({alias}.\"订单类型\", '') IN ({_PROJECT_ORDER_LIST})"
    category = f"COALESCE({alias}.\"核算大类名称\", '')"
    service = (
        f"({category} IN ({_SERVICE_CLOUD_NAMES_LIST}) "
        f"OR {category} LIKE '%软件%' OR {category} LIKE '%服务%')"
    )
    if business_type == "PROJECT":
        return project
    if business_type == "SERVICE_CLOUD":
        return f"NOT ({project}) AND {service}"
    return f"NOT ({project}) AND NOT ({service})"


__all__ = [
    "BusinessType",
    "PROJECT_ORDER_TYPES",
    "business_type_condition",
    "order_business_type",
]

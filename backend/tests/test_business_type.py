"""交易级业务类型判定测试。"""

from ict_agent.business_type import business_type_condition, order_business_type


def test_order_business_type_classifies() -> None:
    assert order_business_type("信产项目N", "IT-存储") == "PROJECT"
    assert order_business_type("信产项目S", "计算-计算") == "PROJECT"
    assert order_business_type("信产常规销售订单", "IPHONE") == "DISTRIBUTION"
    assert order_business_type("哆啦有货常规销售订单", "雷蛇") == "DISTRIBUTION"
    assert order_business_type("信息产品整机销售订单", "微软Azure") == "SERVICE_CLOUD"
    assert order_business_type("信息产品整机销售订单", "西门子工业软件") == "SERVICE_CLOUD"
    assert order_business_type("信息产品整机销售订单", "长虹专业服务") == "SERVICE_CLOUD"
    assert order_business_type("信产项目N", "微软Azure") == "PROJECT"


def test_business_type_conditions_are_mutually_exclusive() -> None:
    project = business_type_condition("s", "PROJECT")
    service = business_type_condition("s", "SERVICE_CLOUD")
    distribution = business_type_condition("s", "DISTRIBUTION")

    assert 's."订单类型"' in project
    assert "NOT" in service and "软件" in service and "服务" in service
    assert "NOT" in distribution and "微软Azure" in distribution

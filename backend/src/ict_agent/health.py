"""确定性健康度计算引擎（阶段 A：项目风险预警系统）。

按业务类型分支计算健康度，全部由真实业务库只读推导，不调用模型、不依赖模拟数据。

主体两类：
- CUSTOMER：`customer_credit` 中的每个授信客户，按业务类型（项目 / 分销 / 软件服务云）
  选择维度权重；混合客户按项目金额分量插值权重。
- CONTRACT：`contracts` 中带真实项目名称的项目合同（按合同号聚合），用合同级指标。

核心设计：
- 超期维度用「账龄梯度」而非单一超期率，对应管控手段（1-30 催款 / 31-60 发函 /
  61-120 停货 / >120 诉讼）。
- 缺数据的维度不记中性分，而是从总分中剔除并对其余可用维度重新归一化，
  避免「无授信但有大额应收」被中性分掩盖。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ict_agent.business_type import PROJECT_ORDER_TYPES, customer_business_profiles
from ict_agent.data import DuckDBStore

# 分销客户五维权重（合计 100%）
CUSTOMER_DISTRIBUTION_WEIGHTS: dict[str, float] = {
    "payment": 30.0,  # 回款节奏
    "overdue": 25.0,  # 应收超期（账龄梯度）
    "credit": 20.0,  # 授信占用
    "list": 15.0,  # 名单资质
    "activity": 10.0,  # 销售活跃度
}

# 项目客户五维权重（合计 100%）：项目金额大、超期损失重，超期权重更高、授信更低
CUSTOMER_PROJECT_WEIGHTS: dict[str, float] = {
    "overdue": 30.0,
    "payment": 25.0,
    "list": 20.0,
    "credit": 15.0,
    "activity": 10.0,
}

# 软件服务云客户：更看重持续回款，弱化实体销售是否活跃这一信号。
# 当前七表没有续费率、服务可用性等专属字段，不能虚构这些维度。
CUSTOMER_SERVICE_CLOUD_WEIGHTS: dict[str, float] = {
    "payment": 35.0,
    "overdue": 25.0,
    "credit": 20.0,
    "list": 15.0,
    "activity": 5.0,
}

CUSTOMER_DIMENSION_NAMES: dict[str, str] = {
    "payment": "回款节奏",
    "overdue": "应收超期",
    "credit": "授信占用",
    "list": "名单资质",
    "activity": "销售活跃度",
}

# 项目合同四维权重（合计 100%）
CONTRACT_WEIGHTS: dict[str, float] = {
    "overdue": 30.0,  # 应收超期率
    "payment": 25.0,  # 回款进度
    "margin": 20.0,  # 合同毛利
    "term_gap": 15.0,  # 账期偏差
    "concentration": 10.0,  # 敞口集中度
}

CONTRACT_DIMENSION_NAMES: dict[str, str] = {
    "overdue": "应收超期",
    "payment": "回款进度",
    "margin": "合同毛利",
    "term_gap": "账期偏差",
    "concentration": "敞口集中度",
}

# 黑白名单状态编码（customer_credit.黑白名单状态）
_LIST_STATUS_SCORES: dict[int, float] = {0: 80.0, 1: 100.0, 2: 10.0, 3: 40.0}

# 账龄梯度：每桶对应管控手段与得分
# (超期天数上限, 得分, 处置) —— 未超期 100 / 1-30 催款 70 / 31-60 发函 40 /
# 61-120 停货 15 / >120 诉讼 0
_AGING_BUCKETS: tuple[tuple[int | None, float], ...] = (
    (0, 100.0),
    (30, 70.0),
    (60, 40.0),
    (120, 15.0),
    (None, 0.0),
)

# 缺数据维度的中性分（仅当全部维度都缺失时兜底）
_MISSING_NEUTRAL = 60.0
# 应收超期率达到该比例记 0 分
_RECEIVABLE_RATE_AT_ZERO = 0.6
# 授信利用率达到该比例记 0 分（>1 即超额占用）
_UTILIZATION_AT_ZERO = 1.0
# 敞口集中度达到该比例记 0 分（单合同占客户应收过半）
_CONCENTRATION_AT_ZERO = 0.5
# 账期偏差达到该天数记 0 分
_TERM_GAP_AT_ZERO = 60.0


def grade_of(score: float) -> str:
    """按分数判定健康度等级。

    >=80 HEALTHY / 60-79 WATCH / 40-59 WARNING / <40 HIGH_RISK。
    """

    if score >= 80.0:
        return "HEALTHY"
    if score >= 60.0:
        return "WATCH"
    if score >= 40.0:
        return "WARNING"
    return "HIGH_RISK"


def compute_customer_health(store: DuckDBStore) -> list[dict[str, Any]]:
    """对 customer_credit 中的每个客户按业务类型分支计算健康度。"""

    profiles = customer_business_profiles(store)
    return [
        _compute_customer(store, row, profiles.get(str(row["customer_id"])))
        for row in _fetch_customer_rows(store)
    ]


def compute_contract_health(store: DuckDBStore) -> list[dict[str, Any]]:
    """对 contracts 中带真实项目名称的项目合同计算健康度。"""

    contracts = _fetch_project_contracts(store)
    ar_by_contract = _fetch_contract_ar(store)
    payment_by_contract = _fetch_contract_payment(store)
    customer_ar = _fetch_customer_total_ar(store)
    return [
        _compute_contract(store, row, ar_by_contract, payment_by_contract, customer_ar)
        for row in contracts
    ]


def compute_health_scores(store: DuckDBStore) -> list[dict[str, Any]]:
    """合并客户与合同健康度，供批量保存。"""

    return [*compute_customer_health(store), *compute_contract_health(store)]


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _as_float(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator != 0 else None


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _first_row(result: Any) -> tuple[object, ...] | None:
    """取查询结果第一行；无数据返回 None。"""

    if result is None or not result.rows:
        return None
    return tuple(result.rows[0])


def _period_str(value: object) -> str:
    return str(value).split("T", maxsplit=1)[0] if value is not None else ""


def _weighted_total(dimensions: list[dict[str, Any]]) -> float:
    """按可用（非缺失）维度重新归一化求总分；全部缺失时返回中性分。"""

    available = [dim for dim in dimensions if not dim["missing"]]
    if not available:
        return _MISSING_NEUTRAL
    total_weight = sum(float(dim["weight"]) for dim in available)
    if total_weight <= 0:
        return _MISSING_NEUTRAL
    weighted = sum(float(dim["score"]) * float(dim["weight"]) for dim in available)
    return round(weighted / total_weight, 1)


# ---------------------------------------------------------------------------
# 客户健康度
# ---------------------------------------------------------------------------


def _fetch_customer_rows(store: DuckDBStore) -> list[dict[str, object]]:
    result = store.fetch(
        """
        SELECT "客户编号_中台" AS customer_id,
               "客户名称" AS customer_name,
               "授信额度" AS credit_limit,
               "黑白名单状态" AS list_status
        FROM customer_credit
        ORDER BY "客户编号_中台"
        """
    )
    return [
        {
            "customer_id": row[0],
            "customer_name": row[1],
            "credit_limit": row[2],
            "list_status": row[3],
        }
        for row in result.rows
    ]


def _fetch_customer_aging(store: DuckDBStore, customer_id: str) -> dict[str, float] | None:
    """按账龄桶聚合最新应收，返回各桶金额（元）；无应收记录返回 None。"""

    result = store.fetch(
        """
        WITH latest AS (SELECT MAX("快照时间") AS d FROM ar_snapshots),
        aged AS (
            SELECT a."应收金额" AS amt, a."超期天数" AS days
            FROM ar_snapshots a JOIN latest l ON a."快照时间" = l.d
            WHERE a."客户编号" = ?
        )
        SELECT COALESCE(SUM(amt) FILTER (WHERE days <= 0), 0) AS current_amount,
               COALESCE(SUM(amt) FILTER (WHERE days BETWEEN 1 AND 30), 0) AS d1_30,
               COALESCE(SUM(amt) FILTER (WHERE days BETWEEN 31 AND 60), 0) AS d31_60,
               COALESCE(SUM(amt) FILTER (WHERE days BETWEEN 61 AND 120), 0) AS d61_120,
               COALESCE(SUM(amt) FILTER (WHERE days > 120), 0) AS d120p
        FROM aged
        """,
        [customer_id],
    )
    row = _first_row(result)
    if row is None:
        return None
    return {
        "current": _as_float(row[0]),
        "d1_30": _as_float(row[1]),
        "d31_60": _as_float(row[2]),
        "d61_120": _as_float(row[3]),
        "d120p": _as_float(row[4]),
    }


def _fetch_flow_3m(store: DuckDBStore, customer_id: str) -> dict[str, float] | None:
    """近 3 个月销售与回款（以最新应收快照为观察窗口）。"""

    result = store.fetch(
        """
        WITH latest AS (SELECT MAX("快照时间") AS d FROM ar_snapshots),
        sales_3m AS (
            SELECT COALESCE(SUM("销售金额_折扣后_含税"), 0) AS sales
            FROM sales, latest
            WHERE "客户编号" = ?
              AND "出库日期" > d - INTERVAL '3 months'
              AND "出库日期" <= d
        ),
        payments_3m AS (
            SELECT COALESCE(SUM("回款金额"), 0) AS payments
            FROM payments, latest
            WHERE "客户编号" = ?
              AND "回款日期" > d - INTERVAL '3 months'
              AND "回款日期" <= d
        )
        SELECT s.sales, p.payments FROM sales_3m s, payments_3m p
        """,
        [customer_id, customer_id],
    )
    row = _first_row(result)
    if row is None:
        return None
    return {"sales_3m": _as_float(row[0]), "payments_3m": _as_float(row[1])}


def _customer_weights(profile: dict[str, object] | None) -> dict[str, float]:
    """按业务画像决定客户维度权重；混合客户按项目金额分量插值。

    项目、分销、服务云分别使用独立权重；混合客户按各类正向销售金额占比插值。
    退货等负金额不作为权重分母，避免净额为负时产生负权重。无画像时兜底分销权重。
    """

    if profile is None:
        return dict(CUSTOMER_DISTRIBUTION_WEIGHTS)
    project_amount = max(0.0, _as_float(profile["project_amount"]))
    distribution_amount = max(0.0, _as_float(profile["distribution_amount"]))
    service_cloud_amount = max(0.0, _as_float(profile["service_cloud_amount"]))
    total = project_amount + distribution_amount + service_cloud_amount
    if total <= 0:
        return dict(CUSTOMER_DISTRIBUTION_WEIGHTS)
    ratios = {
        "project": project_amount / total,
        "distribution": distribution_amount / total,
        "service_cloud": service_cloud_amount / total,
    }
    return {
        key: CUSTOMER_PROJECT_WEIGHTS[key] * ratios["project"]
        + CUSTOMER_DISTRIBUTION_WEIGHTS[key] * ratios["distribution"]
        + CUSTOMER_SERVICE_CLOUD_WEIGHTS[key] * ratios["service_cloud"]
        for key in CUSTOMER_DISTRIBUTION_WEIGHTS
    }


def _aging_score(buckets: dict[str, float] | None) -> tuple[float, bool]:
    """账龄梯度维度：按各桶应收金额加权平均，映射管控手段得分。"""

    if buckets is None:
        return _MISSING_NEUTRAL, True
    total = sum(buckets.values())
    if total <= 0:
        return _MISSING_NEUTRAL, True
    weighted = (
        buckets["current"] * 100.0
        + buckets["d1_30"] * 70.0
        + buckets["d31_60"] * 40.0
        + buckets["d61_120"] * 15.0
        + buckets["d120p"] * 0.0
    )
    return round(_clamp(weighted / total), 1), False


def _payment_score(flow: dict[str, float] | None) -> tuple[float, bool]:
    """回款节奏：近 3 月回款/销售 >=0.9 记 100，线性下降；无数据记缺失。"""

    if flow is None or (flow["sales_3m"] <= 0 and flow["payments_3m"] <= 0):
        return _MISSING_NEUTRAL, True
    if flow["sales_3m"] <= 0:
        return 100.0, False  # 无新增销售仍有回款视为回款良好
    ratio = flow["payments_3m"] / flow["sales_3m"]
    return round(_clamp(ratio / 0.9 * 100.0), 1), False


def _credit_utilization_score(ar_amount: float, credit_limit_wan: float) -> tuple[float, bool]:
    """授信占用：应收/授信额度 <=0.5 记 100，1.0 记 0；无授信记缺失。"""

    if credit_limit_wan <= 0:
        return _MISSING_NEUTRAL, True
    utilization = ar_amount / (credit_limit_wan * 10000.0)
    if utilization <= 0.5:
        return 100.0, False
    return round(
        _clamp(100.0 - (utilization - 0.5) / (_UTILIZATION_AT_ZERO - 0.5) * 100.0), 1
    ), False


def _list_score(list_status: int | None) -> tuple[float, bool]:
    """名单资质：白名单 100 / 一般 80 / 观察 40 / 黑名单 10；无状态记缺失。"""

    if list_status is None:
        return _MISSING_NEUTRAL, True
    return _LIST_STATUS_SCORES.get(list_status, _MISSING_NEUTRAL), False


def _activity_score(flow: dict[str, float] | None, ar_amount: float) -> tuple[float, bool]:
    """销售活跃度：近 3 月有销售记 100，停购但仍欠款记 0，无应收且停购记缺失。"""

    if flow is not None and flow["sales_3m"] > 0:
        return 100.0, False
    if ar_amount > 0:
        return 0.0, False  # 停购却仍有应收 → 跑路信号
    return _MISSING_NEUTRAL, True


def _compute_customer(
    store: DuckDBStore, credit_row: dict[str, object], profile: dict[str, object] | None
) -> dict[str, Any]:
    customer_id = str(credit_row["customer_id"])
    customer_name = str(credit_row["customer_name"])
    credit_limit_wan = _as_float(credit_row["credit_limit"])
    list_status = _as_int(credit_row["list_status"])

    buckets = _fetch_customer_aging(store, customer_id)
    flow = _fetch_flow_3m(store, customer_id)
    ar_amount = sum(buckets.values()) if buckets else 0.0

    weights = _customer_weights(profile)
    overdue_score, overdue_missing = _aging_score(buckets)
    payment_score, payment_missing = _payment_score(flow)
    credit_score, credit_missing = _credit_utilization_score(ar_amount, credit_limit_wan)
    list_score, list_missing = _list_score(list_status)
    activity_score, activity_missing = _activity_score(flow, ar_amount)

    dimensions: list[dict[str, Any]] = [
        {
            "key": "payment",
            "name": CUSTOMER_DIMENSION_NAMES["payment"],
            "score": payment_score,
            "weight": round(weights["payment"], 2),
            "missing": payment_missing,
        },
        {
            "key": "overdue",
            "name": CUSTOMER_DIMENSION_NAMES["overdue"],
            "score": overdue_score,
            "weight": round(weights["overdue"], 2),
            "missing": overdue_missing,
        },
        {
            "key": "credit",
            "name": CUSTOMER_DIMENSION_NAMES["credit"],
            "score": credit_score,
            "weight": round(weights["credit"], 2),
            "missing": credit_missing,
        },
        {
            "key": "list",
            "name": CUSTOMER_DIMENSION_NAMES["list"],
            "score": list_score,
            "weight": round(weights["list"], 2),
            "missing": list_missing,
        },
        {
            "key": "activity",
            "name": CUSTOMER_DIMENSION_NAMES["activity"],
            "score": activity_score,
            "weight": round(weights["activity"], 2),
            "missing": activity_missing,
        },
    ]
    total = _weighted_total(dimensions)
    business_type = str(profile["business_type"]) if profile else "DISTRIBUTION"

    return {
        "subject_type": "CUSTOMER",
        "subject_id": customer_id,
        "subject_label": customer_name,
        "business_type": business_type,
        "score": total,
        "grade": grade_of(total),
        "dimensions": dimensions,
        "drivers": _customer_drivers(buckets, flow, list_status, credit_limit_wan, ar_amount),
        "trend": _fetch_customer_trend(store, customer_id),
        "computed_at": _now_iso(),
    }


def _customer_drivers(
    buckets: dict[str, float] | None,
    flow: dict[str, float] | None,
    list_status: int | None,
    credit_limit_wan: float,
    ar_amount: float,
) -> dict[str, list[str]]:
    down: list[str] = []
    up: list[str] = []
    if buckets is not None:
        overdue_amount = sum(buckets.values()) - buckets["current"]
        total = sum(buckets.values())
        if total > 0:
            overdue_rate = overdue_amount / total
            if overdue_rate > 0.5:
                down.append(f"应收超期率偏高（{overdue_rate:.1%}）")
            elif overdue_rate <= 0.1:
                up.append(f"应收超期率低（{overdue_rate:.1%}）")
        if buckets["d120p"] > 0:
            down.append(f"存在 {buckets['d120p'] / 10000:.0f} 万元超期 120 天以上应收")
    if flow is not None and flow["sales_3m"] > 0:
        ratio = flow["payments_3m"] / flow["sales_3m"]
        if ratio < 0.5:
            down.append(f"近 3 月回款节奏偏慢（回款/销售 {ratio:.1%}）")
        elif ratio >= 0.9:
            up.append(f"近 3 月回款节奏良好（回款/销售 {ratio:.1%}）")
    if flow is not None and flow["sales_3m"] <= 0 and ar_amount > 0:
        down.append("近 3 月无新增销售但仍持有应收")
    if list_status == 2:
        down.append("处于黑名单")
    elif list_status == 3:
        down.append("处于观察名单")
    elif list_status == 1:
        up.append("处于白名单")
    if credit_limit_wan > 0 and ar_amount > 0:
        utilization = ar_amount / (credit_limit_wan * 10000.0)
        if utilization > 1.0:
            down.append(f"授信占用超标（{utilization:.0%}）")
    return {"down": down, "up": up}


def _fetch_customer_trend(store: DuckDBStore, customer_id: str) -> list[dict[str, object]]:
    """按月度应收超期率反推历史健康度趋势（最多 12 期，升序）。"""

    result = store.fetch(
        """
        SELECT "快照时间" AS period,
               COALESCE(SUM("应收金额"), 0) AS ar_amount,
               COALESCE(SUM("超期应收金额"), 0) AS overdue_amount
        FROM ar_snapshots
        WHERE "客户编号" = ?
        GROUP BY "快照时间"
        ORDER BY "快照时间" DESC
        LIMIT ?
        """,
        [customer_id, 12],
    )
    points: list[dict[str, object]] = []
    for row in reversed(result.rows):
        rate = _ratio(_as_float(row[2]), _as_float(row[1]))
        score, _ = _receivable_rate_score(rate)
        points.append({"period": _period_str(row[0]), "score": round(score, 1)})
    return points


def _receivable_rate_score(overdue_rate: float | None) -> tuple[float, bool]:
    """应收超期率：0% 记 100，达到 60% 记 0。"""

    if overdue_rate is None:
        return _MISSING_NEUTRAL, True
    return round(_clamp(100.0 - overdue_rate / _RECEIVABLE_RATE_AT_ZERO * 100.0), 1), False


# ---------------------------------------------------------------------------
# 合同（项目）健康度
# ---------------------------------------------------------------------------


def _fetch_project_contracts(store: DuckDBStore) -> list[dict[str, object]]:
    """按统一交易分类确认的项目合同，按合同号聚合金额/毛利/账期。"""

    placeholders = ", ".join("?" for _ in PROJECT_ORDER_TYPES)

    result = store.fetch(
        f"""
        SELECT "合同编号" AS contract_no,
               "项目名称" AS project_name,
               "客户名称" AS customer_name,
               COALESCE(SUM("销售金额"), 0) AS amount,
               SUM("销售金额" * "实际净毛利率_不含税") / NULLIF(SUM("销售金额"), 0) AS margin_rate,
               MAX("实际账期" - "合同文本账期") AS term_gap
        FROM contracts
        WHERE TRIM(COALESCE("项目名称", '')) <> ''
          AND EXISTS (
              SELECT 1 FROM sales s
              WHERE s."合同号" = contracts."合同编号"
                AND s."订单类型" IN ({placeholders})
          )
        GROUP BY 1, 2, 3
        ORDER BY 1
        """,
        list(PROJECT_ORDER_TYPES),
    )
    return [
        {
            "contract_no": row[0],
            "project_name": row[1],
            "customer_name": row[2],
            "amount": _as_float(row[3]),
            "margin_rate": _as_float(row[4]) if row[4] is not None else None,
            "term_gap": _as_float(row[5]) if row[5] is not None else None,
        }
        for row in result.rows
    ]


def _fetch_contract_ar(store: DuckDBStore) -> dict[str, dict[str, float]]:
    """按合同号聚合最新期末应收与超期。"""

    placeholders = ", ".join("?" for _ in PROJECT_ORDER_TYPES)

    result = store.fetch(
        f"""
        WITH latest AS (SELECT MAX("快照时间") AS d FROM ar_snapshots)
        SELECT a."合同号",
               COALESCE(SUM(a."应收金额"), 0),
               COALESCE(SUM(a."超期应收金额"), 0)
        FROM ar_snapshots a JOIN latest l ON a."快照时间" = l.d
        WHERE EXISTS (
                  SELECT 1 FROM contracts c
                  WHERE c."合同编号" = a."合同号"
                    AND TRIM(COALESCE(c."项目名称", '')) <> ''
              )
          AND EXISTS (
                  SELECT 1 FROM sales s
                  WHERE s."合同号" = a."合同号"
                    AND s."订单类型" IN ({placeholders})
              )
        GROUP BY 1
        """,
        list(PROJECT_ORDER_TYPES),
    )
    return {
        str(row[0]): {"ar": _as_float(row[1]), "overdue": _as_float(row[2])} for row in result.rows
    }


def _fetch_contract_payment(store: DuckDBStore) -> dict[str, float]:
    """按合同号聚合回款金额（元）。"""

    placeholders = ", ".join("?" for _ in PROJECT_ORDER_TYPES)

    result = store.fetch(
        f"""
        SELECT p."合同号", COALESCE(SUM(p."回款金额"), 0)
        FROM payments p
        WHERE EXISTS (
                  SELECT 1 FROM contracts c
                  WHERE c."合同编号" = p."合同号"
                    AND TRIM(COALESCE(c."项目名称", '')) <> ''
              )
          AND EXISTS (
                  SELECT 1 FROM sales s
                  WHERE s."合同号" = p."合同号"
                    AND s."订单类型" IN ({placeholders})
              )
        GROUP BY 1
        """,
        list(PROJECT_ORDER_TYPES),
    )
    return {str(row[0]): _as_float(row[1]) for row in result.rows}


def _fetch_customer_total_ar(store: DuckDBStore) -> dict[str, float]:
    """按客户名称聚合最新期末应收总额，供敞口集中度使用。"""

    result = store.fetch(
        """
        WITH latest AS (SELECT MAX("快照时间") AS d FROM ar_snapshots)
        SELECT a."客户名称", COALESCE(SUM(a."应收金额"), 0)
        FROM ar_snapshots a JOIN latest l ON a."快照时间" = l.d
        WHERE a."客户名称" IS NOT NULL AND a."客户名称" <> ''
        GROUP BY 1
        """
    )
    return {str(row[0]): _as_float(row[1]) for row in result.rows}


def _contract_overdue_score(ar: dict[str, float] | None) -> tuple[float, bool]:
    """合同级应收超期率。"""

    if ar is None or ar["ar"] <= 0:
        return _MISSING_NEUTRAL, True
    return _receivable_rate_score(ar["overdue"] / ar["ar"])


def _contract_payment_score(amount: float, paid: float) -> tuple[float, bool]:
    """回款进度：已回款/合同金额，>=0.9 记 100 线性下降。"""

    if amount <= 0:
        return _MISSING_NEUTRAL, True
    ratio = min(paid / amount, 1.0)
    return round(_clamp(ratio / 0.9 * 100.0), 1), False


def _margin_score(margin_rate: float | None) -> tuple[float, bool]:
    """合同毛利：60 分中性，正毛利加分、负毛利扣分。"""

    if margin_rate is None:
        return _MISSING_NEUTRAL, True
    return round(_clamp(_MISSING_NEUTRAL + margin_rate * 200.0), 1), False


def _term_gap_score(term_gap: float | None) -> tuple[float, bool]:
    """账期偏差：实际账期 vs 合同文本账期，超期 60 天记 0。"""

    if term_gap is None:
        return _MISSING_NEUTRAL, True
    if term_gap <= 0:
        return 100.0, False
    return round(_clamp(100.0 - term_gap / _TERM_GAP_AT_ZERO * 100.0), 1), False


def _concentration_score(contract_ar: float, customer_ar: float) -> tuple[float, bool]:
    """敞口集中度：单合同应收占客户应收比重，>=0.5 记 0。"""

    if contract_ar <= 0 or customer_ar <= 0:
        return _MISSING_NEUTRAL, True
    concentration = contract_ar / customer_ar
    return round(_clamp(100.0 - concentration / _CONCENTRATION_AT_ZERO * 100.0), 1), False


def _compute_contract(
    store: DuckDBStore,
    row: dict[str, object],
    ar_by_contract: dict[str, dict[str, float]],
    payment_by_contract: dict[str, float],
    customer_ar: dict[str, float],
) -> dict[str, Any]:
    contract_no = str(row["contract_no"])
    project_name = str(row["project_name"])
    customer_name = str(row["customer_name"])
    amount = _as_float(row["amount"])
    margin_rate = _as_float(row["margin_rate"]) if row["margin_rate"] is not None else None
    term_gap = _as_float(row["term_gap"]) if row["term_gap"] is not None else None

    ar = ar_by_contract.get(contract_no)
    paid = payment_by_contract.get(contract_no, 0.0)
    total_ar = customer_ar.get(customer_name, 0.0)

    overdue_score, overdue_missing = _contract_overdue_score(ar)
    payment_score, payment_missing = _contract_payment_score(amount, paid)
    margin_score, margin_missing = _margin_score(margin_rate)
    term_score, term_missing = _term_gap_score(term_gap)
    contract_ar = ar["ar"] if ar is not None else 0.0
    concentration_score, concentration_missing = _concentration_score(contract_ar, total_ar)

    dimensions: list[dict[str, Any]] = [
        {
            "key": "overdue",
            "name": CONTRACT_DIMENSION_NAMES["overdue"],
            "score": overdue_score,
            "weight": CONTRACT_WEIGHTS["overdue"],
            "missing": overdue_missing,
        },
        {
            "key": "payment",
            "name": CONTRACT_DIMENSION_NAMES["payment"],
            "score": payment_score,
            "weight": CONTRACT_WEIGHTS["payment"],
            "missing": payment_missing,
        },
        {
            "key": "margin",
            "name": CONTRACT_DIMENSION_NAMES["margin"],
            "score": margin_score,
            "weight": CONTRACT_WEIGHTS["margin"],
            "missing": margin_missing,
        },
        {
            "key": "term_gap",
            "name": CONTRACT_DIMENSION_NAMES["term_gap"],
            "score": term_score,
            "weight": CONTRACT_WEIGHTS["term_gap"],
            "missing": term_missing,
        },
        {
            "key": "concentration",
            "name": CONTRACT_DIMENSION_NAMES["concentration"],
            "score": concentration_score,
            "weight": CONTRACT_WEIGHTS["concentration"],
            "missing": concentration_missing,
        },
    ]
    total = _weighted_total(dimensions)

    return {
        "subject_type": "CONTRACT",
        "subject_id": contract_no,
        "subject_label": project_name,
        "business_type": "PROJECT",
        "score": total,
        "grade": grade_of(total),
        "dimensions": dimensions,
        "drivers": _contract_drivers(ar, payment_score, margin_rate, term_gap, concentration_score),
        "trend": [],
        "computed_at": _now_iso(),
    }


def _contract_drivers(
    ar: dict[str, float] | None,
    payment_score: float,
    margin_rate: float | None,
    term_gap: float | None,
    concentration_score: float,
) -> dict[str, list[str]]:
    down: list[str] = []
    up: list[str] = []
    if ar is not None and ar["ar"] > 0:
        rate = ar["overdue"] / ar["ar"]
        if rate > 0.5:
            down.append(f"应收超期率偏高（{rate:.1%}）")
        elif rate <= 0.1:
            up.append(f"应收超期率低（{rate:.1%}）")
    if payment_score < 50:
        down.append("回款进度明显不足")
    elif payment_score >= 90:
        up.append("回款进度良好")
    if margin_rate is not None and margin_rate < 0:
        down.append(f"合同毛利为负（{margin_rate:.1%}）")
    if term_gap is not None and term_gap >= 60:
        down.append(f"账期偏差 {term_gap:.0f} 天")
    if concentration_score <= 20:
        down.append("单合同敞口集中度过高")
    return {"down": down, "up": up}


__all__ = [
    "CONTRACT_WEIGHTS",
    "CUSTOMER_DISTRIBUTION_WEIGHTS",
    "CUSTOMER_PROJECT_WEIGHTS",
    "CUSTOMER_SERVICE_CLOUD_WEIGHTS",
    "compute_contract_health",
    "compute_customer_health",
    "compute_health_scores",
    "grade_of",
]

"""按“公司 × 业务类型”分轨计算确定性健康度。

同一公司可以分别生成分销、项目、服务云三条记录。业务类型不仅改变权重，也改变
评分维度和底层数据范围；合同只作为项目业务的证据，不再单独生成健康主体。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ict_agent.business_type import (
    BusinessType,
    business_type_condition,
    customer_business_profiles,
)
from ict_agent.data import DuckDBStore

DISTRIBUTION_WEIGHTS = {
    "payment": 30.0,
    "overdue": 25.0,
    "credit": 20.0,
    "list": 15.0,
    "activity": 10.0,
}
PROJECT_WEIGHTS = {
    "overdue": 30.0,
    "payment": 30.0,
    "margin": 20.0,
    "term_gap": 10.0,
    "list": 10.0,
}
SERVICE_CLOUD_WEIGHTS = {
    "payment": 35.0,
    "overdue": 30.0,
    "credit": 15.0,
    "continuity": 10.0,
    "list": 10.0,
}
DIMENSION_NAMES = {
    "payment": "回款节奏",
    "overdue": "应收超期",
    "credit": "授信占用",
    "list": "名单资质",
    "activity": "销售活跃度",
    "margin": "项目毛利",
    "term_gap": "项目账期偏差",
    "continuity": "服务持续性",
}

_LIST_STATUS_SCORES = {0: 80.0, 1: 100.0, 2: 10.0, 3: 40.0}
_MISSING_NEUTRAL = 60.0
_UTILIZATION_AT_ZERO = 1.0
_TERM_GAP_AT_ZERO = 60.0
_RECEIVABLE_RATE_AT_ZERO = 0.6


def grade_of(score: float) -> str:
    if score >= 80.0:
        return "HEALTHY"
    if score >= 60.0:
        return "WATCH"
    if score >= 40.0:
        return "WARNING"
    return "HIGH_RISK"


def compute_health_scores(store: DuckDBStore) -> list[dict[str, Any]]:
    """为每个实际发生过的“授信客户 × 业务类型”生成一条健康结果。"""

    profiles = customer_business_profiles(store)
    results: list[dict[str, Any]] = []
    segments: tuple[tuple[BusinessType, str], ...] = (
        ("DISTRIBUTION", "distribution_order_count"),
        ("PROJECT", "project_order_count"),
        ("SERVICE_CLOUD", "service_cloud_order_count"),
    )
    for customer in _fetch_customer_rows(store):
        customer_id = str(customer["customer_id"])
        profile = profiles.get(customer_id)
        if profile is None:
            continue
        for business_type, count_key in segments:
            if (_as_int(profile.get(count_key, 0)) or 0) > 0:
                results.append(_compute_segment(store, customer, business_type))
    return results


def compute_customer_health(store: DuckDBStore) -> list[dict[str, Any]]:
    """健康记录均为客户业务分段；保留函数名供应用层直接调用。"""

    return compute_health_scores(store)


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


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator != 0 else None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _period_str(value: object) -> str:
    return str(value).split("T", maxsplit=1)[0] if value is not None else ""


def _first_row(result: Any) -> tuple[object, ...] | None:
    if result is None or not result.rows:
        return None
    return tuple(result.rows[0])


def _weighted_total(dimensions: list[dict[str, Any]]) -> float:
    available = [dimension for dimension in dimensions if not dimension["missing"]]
    if not available:
        return _MISSING_NEUTRAL
    total_weight = sum(float(dimension["weight"]) for dimension in available)
    if total_weight <= 0:
        return _MISSING_NEUTRAL
    weighted = sum(
        float(dimension["score"]) * float(dimension["weight"]) for dimension in available
    )
    return round(weighted / total_weight, 1)


def _dimension(
    key: str, score_and_missing: tuple[float, bool], weights: dict[str, float]
) -> dict[str, Any]:
    score, missing = score_and_missing
    return {
        "key": key,
        "name": DIMENSION_NAMES[key],
        "score": score,
        "weight": weights[key],
        "missing": missing,
    }


def _fetch_customer_rows(store: DuckDBStore) -> list[dict[str, object]]:
    result = store.fetch(
        """
        SELECT "客户编号_中台", "客户名称", "授信额度", "黑白名单状态"
        FROM customer_credit ORDER BY "客户编号_中台"
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


def _linked_sales_exists(record_alias: str, business_type: BusinessType) -> str:
    return f"""
        EXISTS (
            SELECT 1 FROM sales s
            WHERE s."客户编号" = {record_alias}."客户编号"
              AND s."销售订单号" = {record_alias}."销售订单号"
              AND (TRIM(COALESCE({record_alias}."合同号", '')) = ''
                   OR s."合同号" = {record_alias}."合同号")
              AND {business_type_condition("s", business_type)}
        )
    """


def _fetch_segment_aging(
    store: DuckDBStore, customer_id: str, business_type: BusinessType
) -> dict[str, float] | None:
    result = store.fetch(
        f"""
        WITH latest AS (SELECT MAX("快照时间") AS d FROM ar_snapshots),
        aged AS (
            SELECT a."应收金额" AS amount, a."超期天数" AS overdue_days
            FROM ar_snapshots a JOIN latest l ON a."快照时间" = l.d
            WHERE a."客户编号" = ? AND {_linked_sales_exists("a", business_type)}
        )
        SELECT COALESCE(SUM(amount) FILTER (WHERE overdue_days <= 0), 0),
               COALESCE(SUM(amount) FILTER (WHERE overdue_days BETWEEN 1 AND 30), 0),
               COALESCE(SUM(amount) FILTER (WHERE overdue_days BETWEEN 31 AND 60), 0),
               COALESCE(SUM(amount) FILTER (WHERE overdue_days BETWEEN 61 AND 120), 0),
               COALESCE(SUM(amount) FILTER (WHERE overdue_days > 120), 0)
        FROM aged
        """,
        [customer_id],
    )
    row = _first_row(result)
    if row is None:
        return None
    buckets = {
        "current": _as_float(row[0]),
        "d1_30": _as_float(row[1]),
        "d31_60": _as_float(row[2]),
        "d61_120": _as_float(row[3]),
        "d120p": _as_float(row[4]),
    }
    return buckets if sum(buckets.values()) != 0 else None


def _fetch_segment_flow(
    store: DuckDBStore,
    customer_id: str,
    business_type: BusinessType,
    *,
    recent_months: int | None,
) -> dict[str, float] | None:
    sales_window = ""
    payment_window = ""
    if recent_months is not None:
        sales_window = (
            f'AND s."出库日期" > l.d - INTERVAL \'{recent_months} months\' AND s."出库日期" <= l.d'
        )
        payment_window = (
            f'AND p."回款日期" > l.d - INTERVAL \'{recent_months} months\' AND p."回款日期" <= l.d'
        )
    result = store.fetch(
        f"""
        WITH latest AS (SELECT MAX("快照时间") AS d FROM ar_snapshots),
        segment_sales AS (
            SELECT COALESCE(SUM(s."销售金额_折扣后_含税"), 0) AS amount
            FROM sales s, latest l
            WHERE s."客户编号" = ? AND {business_type_condition("s", business_type)}
              {sales_window}
        ),
        segment_payments AS (
            SELECT COALESCE(SUM(p."回款金额"), 0) AS amount
            FROM payments p, latest l
            WHERE p."客户编号" = ? AND {_linked_sales_exists("p", business_type)}
              {payment_window}
        )
        SELECT s.amount, p.amount FROM segment_sales s, segment_payments p
        """,
        [customer_id, customer_id],
    )
    row = _first_row(result)
    if row is None:
        return None
    return {"sales": _as_float(row[0]), "payments": _as_float(row[1])}


def _fetch_service_continuity(store: DuckDBStore, customer_id: str) -> tuple[float, bool]:
    result = store.fetch(
        f"""
        WITH latest AS (SELECT MAX("快照时间") AS d FROM ar_snapshots)
        SELECT COUNT(DISTINCT DATE_TRUNC('month', s."出库日期"))
        FROM sales s, latest l
        WHERE s."客户编号" = ? AND {business_type_condition("s", "SERVICE_CLOUD")}
          AND s."出库日期" > l.d - INTERVAL '6 months' AND s."出库日期" <= l.d
          AND s."销售金额_折扣后_含税" > 0
        """,
        [customer_id],
    )
    row = _first_row(result)
    if row is None:
        return _MISSING_NEUTRAL, True
    return round(_clamp(_as_float(row[0]) / 6.0 * 100.0), 1), False


def _fetch_project_contract_metrics(
    store: DuckDBStore, customer_id: str
) -> dict[str, float | None]:
    result = store.fetch(
        f"""
        WITH project_contracts AS (
            SELECT DISTINCT s."合同号" AS contract_no FROM sales s
            WHERE s."客户编号" = ? AND TRIM(COALESCE(s."合同号", '')) <> ''
              AND {business_type_condition("s", "PROJECT")}
        )
        SELECT SUM(c."销售金额" * c."实际净毛利率_不含税")
                   / NULLIF(SUM(c."销售金额"), 0),
               MAX(c."实际账期" - c."合同文本账期")
        FROM contracts c JOIN project_contracts p ON p.contract_no = c."合同编号"
        """,
        [customer_id],
    )
    row = _first_row(result)
    if row is None:
        return {"margin_rate": None, "term_gap": None}
    return {
        "margin_rate": _as_float(row[0]) if row[0] is not None else None,
        "term_gap": _as_float(row[1]) if row[1] is not None else None,
    }


def _aging_score(buckets: dict[str, float] | None) -> tuple[float, bool]:
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
    )
    return round(_clamp(weighted / total), 1), False


def _payment_score(flow: dict[str, float] | None) -> tuple[float, bool]:
    if flow is None or (flow["sales"] <= 0 and flow["payments"] <= 0):
        return _MISSING_NEUTRAL, True
    if flow["sales"] <= 0:
        return 100.0, False
    return round(_clamp(flow["payments"] / flow["sales"] / 0.9 * 100.0), 1), False


def _credit_score(ar_amount: float, credit_limit_wan: float) -> tuple[float, bool]:
    if credit_limit_wan <= 0:
        return _MISSING_NEUTRAL, True
    utilization = ar_amount / (credit_limit_wan * 10000.0)
    if utilization <= 0.5:
        return 100.0, False
    return round(
        _clamp(100.0 - (utilization - 0.5) / (_UTILIZATION_AT_ZERO - 0.5) * 100.0), 1
    ), False


def _list_score(list_status: int | None) -> tuple[float, bool]:
    if list_status is None:
        return _MISSING_NEUTRAL, True
    return _LIST_STATUS_SCORES.get(list_status, _MISSING_NEUTRAL), False


def _activity_score(flow: dict[str, float] | None, ar_amount: float) -> tuple[float, bool]:
    if flow is not None and flow["sales"] > 0:
        return 100.0, False
    if ar_amount > 0:
        return 0.0, False
    return _MISSING_NEUTRAL, True


def _margin_score(margin_rate: float | None) -> tuple[float, bool]:
    if margin_rate is None:
        return _MISSING_NEUTRAL, True
    return round(_clamp(_MISSING_NEUTRAL + margin_rate * 200.0), 1), False


def _term_gap_score(term_gap: float | None) -> tuple[float, bool]:
    if term_gap is None:
        return _MISSING_NEUTRAL, True
    if term_gap <= 0:
        return 100.0, False
    return round(_clamp(100.0 - term_gap / _TERM_GAP_AT_ZERO * 100.0), 1), False


def _receivable_rate_score(overdue_rate: float | None) -> tuple[float, bool]:
    if overdue_rate is None:
        return _MISSING_NEUTRAL, True
    return round(_clamp(100.0 - overdue_rate / _RECEIVABLE_RATE_AT_ZERO * 100.0), 1), False


def _compute_segment(
    store: DuckDBStore, customer: dict[str, object], business_type: BusinessType
) -> dict[str, Any]:
    customer_id = str(customer["customer_id"])
    credit_limit = _as_float(customer["credit_limit"])
    list_status = _as_int(customer["list_status"])
    buckets = _fetch_segment_aging(store, customer_id, business_type)
    ar_amount = sum(buckets.values()) if buckets else 0.0

    if business_type == "PROJECT":
        flow = _fetch_segment_flow(store, customer_id, business_type, recent_months=None)
        contract = _fetch_project_contract_metrics(store, customer_id)
        dimensions = [
            _dimension("overdue", _aging_score(buckets), PROJECT_WEIGHTS),
            _dimension("payment", _payment_score(flow), PROJECT_WEIGHTS),
            _dimension("margin", _margin_score(contract["margin_rate"]), PROJECT_WEIGHTS),
            _dimension("term_gap", _term_gap_score(contract["term_gap"]), PROJECT_WEIGHTS),
            _dimension("list", _list_score(list_status), PROJECT_WEIGHTS),
        ]
        drivers = _project_drivers(buckets, flow, contract, list_status)
    elif business_type == "SERVICE_CLOUD":
        flow = _fetch_segment_flow(store, customer_id, business_type, recent_months=3)
        continuity = _fetch_service_continuity(store, customer_id)
        dimensions = [
            _dimension("payment", _payment_score(flow), SERVICE_CLOUD_WEIGHTS),
            _dimension("overdue", _aging_score(buckets), SERVICE_CLOUD_WEIGHTS),
            _dimension("credit", _credit_score(ar_amount, credit_limit), SERVICE_CLOUD_WEIGHTS),
            _dimension("continuity", continuity, SERVICE_CLOUD_WEIGHTS),
            _dimension("list", _list_score(list_status), SERVICE_CLOUD_WEIGHTS),
        ]
        drivers = _service_drivers(buckets, flow, continuity, list_status)
    else:
        flow = _fetch_segment_flow(store, customer_id, business_type, recent_months=3)
        dimensions = [
            _dimension("payment", _payment_score(flow), DISTRIBUTION_WEIGHTS),
            _dimension("overdue", _aging_score(buckets), DISTRIBUTION_WEIGHTS),
            _dimension("credit", _credit_score(ar_amount, credit_limit), DISTRIBUTION_WEIGHTS),
            _dimension("list", _list_score(list_status), DISTRIBUTION_WEIGHTS),
            _dimension("activity", _activity_score(flow, ar_amount), DISTRIBUTION_WEIGHTS),
        ]
        drivers = _distribution_drivers(buckets, flow, list_status, ar_amount)

    total = _weighted_total(dimensions)
    return {
        "subject_id": customer_id,
        "subject_label": str(customer["customer_name"]),
        "business_type": business_type,
        "score": total,
        "grade": grade_of(total),
        "dimensions": dimensions,
        "drivers": drivers,
        "trend": _fetch_segment_trend(store, customer_id, business_type),
        "computed_at": _now_iso(),
    }


def _common_drivers(
    buckets: dict[str, float] | None,
    flow: dict[str, float] | None,
    list_status: int | None,
) -> dict[str, list[str]]:
    down: list[str] = []
    up: list[str] = []
    if buckets is not None:
        total = sum(buckets.values())
        overdue = total - buckets["current"]
        if total > 0 and overdue / total > 0.5:
            down.append(f"应收超期率偏高（{overdue / total:.1%}）")
        if buckets["d120p"] > 0:
            down.append(f"存在 {buckets['d120p'] / 10000:.0f} 万元超期 120 天以上应收")
    if flow is not None and flow["sales"] > 0:
        ratio = flow["payments"] / flow["sales"]
        if ratio < 0.5:
            down.append(f"回款节奏偏慢（回款/销售 {ratio:.1%}）")
        elif ratio >= 0.9:
            up.append(f"回款节奏良好（回款/销售 {ratio:.1%}）")
    if list_status == 2:
        down.append("处于黑名单")
    elif list_status == 3:
        down.append("处于观察名单")
    elif list_status == 1:
        up.append("处于白名单")
    return {"down": down, "up": up}


def _distribution_drivers(
    buckets: dict[str, float] | None,
    flow: dict[str, float] | None,
    list_status: int | None,
    ar_amount: float,
) -> dict[str, list[str]]:
    drivers = _common_drivers(buckets, flow, list_status)
    if flow is not None and flow["sales"] <= 0 and ar_amount > 0:
        drivers["down"].append("近 3 月无分销销售但仍持有该类应收")
    return drivers


def _project_drivers(
    buckets: dict[str, float] | None,
    flow: dict[str, float] | None,
    contract: dict[str, float | None],
    list_status: int | None,
) -> dict[str, list[str]]:
    drivers = _common_drivers(buckets, flow, list_status)
    margin = contract["margin_rate"]
    term_gap = contract["term_gap"]
    if margin is not None and margin < 0:
        drivers["down"].append(f"项目合同毛利为负（{margin:.1%}）")
    if term_gap is not None and term_gap >= 60:
        drivers["down"].append(f"项目账期偏差 {term_gap:.0f} 天")
    return drivers


def _service_drivers(
    buckets: dict[str, float] | None,
    flow: dict[str, float] | None,
    continuity: tuple[float, bool],
    list_status: int | None,
) -> dict[str, list[str]]:
    drivers = _common_drivers(buckets, flow, list_status)
    score, missing = continuity
    if not missing and score <= 16.7:
        drivers["down"].append("近 6 月服务云交易仅覆盖 1 个月")
    elif not missing and score >= 400.0 / 6.0:
        drivers["up"].append("近 6 月服务云交易持续性较好")
    return drivers


def _fetch_segment_trend(
    store: DuckDBStore, customer_id: str, business_type: BusinessType
) -> list[dict[str, object]]:
    result = store.fetch(
        f"""
        SELECT a."快照时间", COALESCE(SUM(a."应收金额"), 0),
               COALESCE(SUM(a."超期应收金额"), 0)
        FROM ar_snapshots a
        WHERE a."客户编号" = ? AND {_linked_sales_exists("a", business_type)}
        GROUP BY 1 ORDER BY 1 DESC LIMIT 12
        """,
        [customer_id],
    )
    points: list[dict[str, object]] = []
    for row in reversed(result.rows):
        rate = _ratio(_as_float(row[2]), _as_float(row[1]))
        score, _ = _receivable_rate_score(rate)
        points.append({"period": _period_str(row[0]), "score": round(score, 1)})
    return points


__all__ = [
    "DISTRIBUTION_WEIGHTS",
    "PROJECT_WEIGHTS",
    "SERVICE_CLOUD_WEIGHTS",
    "compute_customer_health",
    "compute_health_scores",
    "grade_of",
]

"""确定性健康度计算引擎（阶段 A：项目风险预警系统）。

为名单建议与预警提供客户级与项目级（合同级）健康度输入。
健康度全部由确定性指标计算（复用业务库只读查询与模拟数据），
绝不调用模型生成分数；返回纯 dict/list，不依赖 Pydantic。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from ict_agent.data import DuckDBStore
from ict_agent.simdata import SimulatedData, SimulatedProjectStage

# 客户健康度五维权重（合计 100%，舆情维度已随舆情模块移除）
HEALTH_WEIGHTS: dict[str, float] = {
    "payment": 33.33,  # 回款节奏
    "progress": 22.22,  # 项目进度
    "receivable": 16.67,  # 应收超期
    "credit": 16.67,  # 合同授信/名单
    "guarantor": 11.11,  # 客户担保人
}

CUSTOMER_DIMENSION_NAMES: dict[str, str] = {
    "payment": "回款节奏",
    "progress": "项目进度",
    "receivable": "应收超期",
    "credit": "合同授信",
    "guarantor": "客户担保人",
}

# 合同（项目）健康度四维权重（合计 100%，舆情维度已随舆情模块移除）
CONTRACT_HEALTH_WEIGHTS: dict[str, float] = {
    "progress": 44.44,  # 项目进度（阶段 + 里程碑）
    "payment": 27.78,  # 计划回款
    "contract": 16.67,  # 合同毛利
    "guarantor": 11.11,  # 担保人
}

CONTRACT_DIMENSION_NAMES: dict[str, str] = {
    "progress": "项目进度",
    "payment": "计划回款",
    "contract": "合同毛利",
    "guarantor": "担保人",
}

# 项目阶段基础分
_STAGE_BASE_SCORES: dict[str, float] = {
    "立项": 80.0,
    "执行": 60.0,
    "验收": 40.0,
    "回款": 30.0,
    "结束": 100.0,
}

# 黑白名单状态编码（customer_credit.黑白名单状态）
_LIST_STATUS_LABELS: dict[int, str] = {0: "GENERAL", 1: "WHITE", 2: "BLACK", 3: "WATCH"}
_LIST_STATUS_SCORES: dict[int, float] = {0: 80.0, 1: 100.0, 2: 10.0, 3: 40.0}

# 担保人异常状态集合（sim_guarantors.担保人状态）
_ABNORMAL_GUARANTOR_STATUS: frozenset[str] = frozenset(
    {"经营异常", "失联待核验", "待核验", "失信", "注销", "逾期"}
)

# 缺数据维度的中性分
_MISSING_NEUTRAL = 60.0
# 应收超期率达到该比例记 0 分
_RECEIVABLE_RATE_AT_ZERO = 0.6


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


def compute_customer_health(store: DuckDBStore, sim: SimulatedData) -> list[dict[str, Any]]:
    """对 customer_credit 中的每个客户计算六维健康度。"""

    return [_compute_customer(store, sim, row) for row in _fetch_customer_rows(store)]


def compute_contract_health(store: DuckDBStore, sim: SimulatedData) -> list[dict[str, Any]]:
    """对 sim.project_stages 中的真实合同计算项目健康度。"""

    return [_compute_contract(store, sim, stage) for stage in sim.project_stages]


def compute_health_scores(store: DuckDBStore, sim: SimulatedData) -> list[dict[str, Any]]:
    """合并客户与合同健康度，供批量保存。"""

    return [*compute_customer_health(store, sim), *compute_contract_health(store, sim)]


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


def _today() -> date:
    """当前日期，供计划回款临近/逾期判定；测试可替换。"""

    return datetime.now(UTC).date()


def _first_row(
    result: Any,
) -> tuple[object, ...] | None:
    """取查询结果第一行；无数据返回 None。"""

    if result is None or not result.rows:
        return None
    return tuple(result.rows[0])


def _period_str(value: object) -> str:
    return str(value).split("T", maxsplit=1)[0] if value is not None else ""


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
    rows: list[dict[str, object]] = []
    for row in result.rows:
        rows.append(
            {
                "customer_id": row[0],
                "customer_name": row[1],
                "credit_limit": row[2],
                "list_status": row[3],
            }
        )
    return rows


def _fetch_latest_ar(store: DuckDBStore, customer_id: str) -> dict[str, object] | None:
    result = store.fetch(
        """
        WITH latest AS (
            SELECT MAX("快照时间") AS p FROM ar_snapshots WHERE "客户编号" = ?
        )
        SELECT l.p AS period,
               COALESCE(SUM(a."应收金额"), 0) AS ar_amount,
               COALESCE(SUM(a."超期应收金额"), 0) AS overdue_amount,
               COALESCE(SUM(a."超期60天以上金额"), 0) AS overdue_60_amount
        FROM ar_snapshots a
        JOIN latest l ON a."快照时间" = l.p
        WHERE a."客户编号" = ?
        GROUP BY l.p
        """,
        [customer_id, customer_id],
    )
    row = _first_row(result)
    if row is None:
        return None
    return {
        "period": _period_str(row[0]),
        "ar_amount": _as_float(row[1]),
        "overdue_amount": _as_float(row[2]),
        "overdue_60_amount": _as_float(row[3]),
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


def _fetch_customer_margin(store: DuckDBStore, customer_name: str) -> float | None:
    result = store.fetch(
        """
        SELECT SUM("销售金额" * "实际净毛利率_不含税") / NULLIF(SUM("销售金额"), 0)
            AS margin_rate
        FROM contracts
        WHERE "客户名称" = ?
        """,
        [customer_name],
    )
    row = _first_row(result)
    if row is None or row[0] is None:
        return None
    return _as_float(row[0])


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
    receivable_weight = HEALTH_WEIGHTS["receivable"]
    neutral = (100.0 - receivable_weight) * _MISSING_NEUTRAL
    for row in reversed(result.rows):
        rate = _ratio(_as_float(row[2]), _as_float(row[1]))
        rec_score, _ = _receivable_score(rate)
        overall = (receivable_weight * rec_score + neutral) / 100.0
        points.append({"period": _period_str(row[0]), "score": round(overall, 1)})
    return points


def _compute_customer(
    store: DuckDBStore, sim: SimulatedData, credit_row: dict[str, object]
) -> dict[str, Any]:
    customer_id = str(credit_row["customer_id"])
    customer_name = str(credit_row["customer_name"])
    credit_limit_wan = _as_float(credit_row["credit_limit"])
    list_status = _as_int(credit_row["list_status"])

    latest_ar = _fetch_latest_ar(store, customer_id)
    flow = _fetch_flow_3m(store, customer_id)
    margin_rate = _fetch_customer_margin(store, customer_name)
    guarantor_status = _worst_guarantor_status(_guarantors_of(sim, customer_id, customer_name))
    stages = [s for s in sim.project_stages if s.customer_name == customer_name]

    ar_amount = _as_float(latest_ar["ar_amount"]) if latest_ar else 0.0
    overdue_rate = _ratio(_as_float(latest_ar["overdue_amount"]), ar_amount) if latest_ar else None
    collection_ratio = (
        _ratio(flow["payments_3m"], flow["sales_3m"])
        if flow is not None and flow["sales_3m"] > 0
        else None
    )
    utilization = _ratio(ar_amount, credit_limit_wan * 10000.0) if credit_limit_wan > 0 else None

    receivable_score, receivable_missing = _receivable_score(overdue_rate)
    payment_score, payment_missing = _payment_score(flow)
    credit_score, credit_missing = _credit_score(utilization, list_status, margin_rate)
    guarantor_score, guarantor_missing = _guarantor_score(guarantor_status)
    progress_score, progress_missing = _progress_score(stages)

    dimensions: list[dict[str, Any]] = [
        {
            "key": "payment",
            "name": CUSTOMER_DIMENSION_NAMES["payment"],
            "score": payment_score,
            "weight": HEALTH_WEIGHTS["payment"],
            "missing": payment_missing,
        },
        {
            "key": "progress",
            "name": CUSTOMER_DIMENSION_NAMES["progress"],
            "score": progress_score,
            "weight": HEALTH_WEIGHTS["progress"],
            "missing": progress_missing,
        },
        {
            "key": "receivable",
            "name": CUSTOMER_DIMENSION_NAMES["receivable"],
            "score": receivable_score,
            "weight": HEALTH_WEIGHTS["receivable"],
            "missing": receivable_missing,
        },
        {
            "key": "credit",
            "name": CUSTOMER_DIMENSION_NAMES["credit"],
            "score": credit_score,
            "weight": HEALTH_WEIGHTS["credit"],
            "missing": credit_missing,
        },
        {
            "key": "guarantor",
            "name": CUSTOMER_DIMENSION_NAMES["guarantor"],
            "score": guarantor_score,
            "weight": HEALTH_WEIGHTS["guarantor"],
            "missing": guarantor_missing,
        },
    ]
    total = round(sum(d["score"] * d["weight"] for d in dimensions) / 100.0, 1)

    return {
        "subject_type": "CUSTOMER",
        "subject_id": customer_id,
        "subject_label": customer_name,
        "score": total,
        "grade": grade_of(total),
        "dimensions": dimensions,
        "drivers": _customer_drivers(
            overdue_rate,
            collection_ratio,
            list_status,
            margin_rate,
            guarantor_status,
            stages,
        ),
        "trend": _fetch_customer_trend(store, customer_id),
        "computed_at": _now_iso(),
    }


def _receivable_score(overdue_rate: float | None) -> tuple[float, bool]:
    """应收超期率：0% 记 100，达到 60% 记 0。"""

    if overdue_rate is None:
        return _MISSING_NEUTRAL, True
    return round(_clamp(100.0 - overdue_rate / _RECEIVABLE_RATE_AT_ZERO * 100.0), 1), False


def _payment_score(flow: dict[str, float] | None) -> tuple[float, bool]:
    """近 3 月回款节奏：回款/销售 >=1 记 100，否则线性；无数据记中性。"""

    if flow is None or (flow["sales_3m"] <= 0 and flow["payments_3m"] <= 0):
        return _MISSING_NEUTRAL, True
    if flow["sales_3m"] <= 0:
        # 无新增销售仍有回款视为回款良好
        return 100.0, False
    ratio = flow["payments_3m"] / flow["sales_3m"]
    return round(_clamp(ratio * 100.0), 1), False


def _credit_score(
    utilization: float | None, list_status: int | None, margin_rate: float | None
) -> tuple[float, bool]:
    """合同授信维度 = 授信利用率 + 名单状态 + 合同毛利 的可用子分均值。"""

    parts: list[float] = []
    if utilization is not None:
        if utilization <= 0.5:
            parts.append(100.0)
        else:
            parts.append(max(0.0, 100.0 - (utilization - 0.5) * 200.0))
    if list_status is not None:
        parts.append(_LIST_STATUS_SCORES.get(list_status, _MISSING_NEUTRAL))
    if margin_rate is not None:
        parts.append(round(_clamp(_MISSING_NEUTRAL + margin_rate * 200.0), 1))
    if not parts:
        return _MISSING_NEUTRAL, True
    return round(sum(parts) / len(parts), 1), False


def _guarantor_score(worst_status: str | None) -> tuple[float, bool]:
    """担保人维度：无担保人记中性，状态异常记 30，正常记 100。"""

    if worst_status is None:
        return _MISSING_NEUTRAL, True
    if worst_status == "正常":
        return 100.0, False
    if worst_status in _ABNORMAL_GUARANTOR_STATUS:
        return 30.0, False
    return 60.0, False


def _progress_score(stages: list[SimulatedProjectStage]) -> tuple[float, bool]:
    """项目进度维度：按客户名下项目里程碑进度均值。"""

    if not stages:
        return _MISSING_NEUTRAL, True
    average = sum(stage.milestone_progress for stage in stages) / len(stages)
    return round(_clamp(average), 1), False


def _guarantors_of(sim: SimulatedData, customer_id: str, customer_name: str) -> list[Any]:
    return [
        g
        for g in sim.guarantors
        if g.customer_id == customer_id or g.customer_name == customer_name
    ]


def _worst_guarantor_status(guarantors: list[Any]) -> str | None:
    if not guarantors:
        return None
    statuses = [str(g.guarantor_status) for g in guarantors]
    if any(status == "正常" for status in statuses) and all(
        status == "正常" for status in statuses
    ):
        return "正常"
    for status in ("经营异常", "失联待核验", "待核验", "失信", "注销", "逾期"):
        if status in statuses:
            return status
    return statuses[0]


def _customer_drivers(
    overdue_rate: float | None,
    collection_ratio: float | None,
    list_status: int | None,
    margin_rate: float | None,
    guarantor_status: str | None,
    stages: list[SimulatedProjectStage],
) -> dict[str, list[str]]:
    down: list[str] = []
    up: list[str] = []
    if overdue_rate is not None and overdue_rate > 0.5:
        down.append(f"应收超期率偏高（{overdue_rate:.1%}）")
    elif overdue_rate is not None and overdue_rate <= 0.1:
        up.append(f"应收超期率低（{overdue_rate:.1%}）")
    if collection_ratio is not None and collection_ratio < 0.5:
        down.append(f"近 3 月回款节奏偏慢（回款/销售 {collection_ratio:.1%}）")
    elif collection_ratio is not None and collection_ratio >= 0.9:
        up.append(f"近 3 月回款节奏良好（回款/销售 {collection_ratio:.1%}）")
    if list_status == 2:
        down.append("处于黑名单")
    elif list_status == 3:
        down.append("处于观察名单")
    elif list_status == 1:
        up.append("处于白名单")
    if margin_rate is not None and margin_rate < 0:
        down.append(f"合同毛利为负（{margin_rate:.1%}）")
    if guarantor_status is not None and guarantor_status != "正常":
        down.append(f"担保人状态异常（{guarantor_status}）")
    elif guarantor_status == "正常":
        up.append("担保人状态正常")
    if stages:
        average = sum(stage.milestone_progress for stage in stages) / len(stages)
        if average >= 80:
            up.append("项目进度正常推进")
        elif average < 30:
            down.append("项目进度滞后")
    return {"down": down, "up": up}


# ---------------------------------------------------------------------------
# 合同（项目）健康度
# ---------------------------------------------------------------------------


def _fetch_contract_margin(store: DuckDBStore, contract_no: str) -> float | None:
    result = store.fetch(
        'SELECT AVG("实际净毛利率_不含税") AS margin_rate FROM contracts WHERE "合同编号" = ?',
        [contract_no],
    )
    row = _first_row(result)
    if row is None or row[0] is None:
        return None
    return _as_float(row[0])


def _compute_contract(
    store: DuckDBStore, sim: SimulatedData, stage: SimulatedProjectStage
) -> dict[str, Any]:
    base = _STAGE_BASE_SCORES.get(stage.stage, _MISSING_NEUTRAL)
    adjustment = _clamp((stage.milestone_progress - 50.0) * 0.4, -15.0, 15.0)
    progress_score = round(_clamp(base + adjustment), 1)

    payment_score, payment_missing = _planned_payment_score(stage.planned_payment_date, _today())
    margin_rate = _fetch_contract_margin(store, stage.contract_no)
    margin_score = (
        round(_clamp(_MISSING_NEUTRAL + margin_rate * 200.0), 1)
        if margin_rate is not None
        else _MISSING_NEUTRAL
    )
    margin_missing = margin_rate is None
    worst_guarantor = _worst_guarantor_status(
        [g for g in sim.guarantors if g.related_project == stage.project_name]
        or _guarantors_of(sim, "", stage.customer_name)
    )
    guarantor_score, guarantor_missing = _guarantor_score(worst_guarantor)

    dimensions: list[dict[str, Any]] = [
        {
            "key": "progress",
            "name": CONTRACT_DIMENSION_NAMES["progress"],
            "score": progress_score,
            "weight": CONTRACT_HEALTH_WEIGHTS["progress"],
            "missing": False,
        },
        {
            "key": "payment",
            "name": CONTRACT_DIMENSION_NAMES["payment"],
            "score": payment_score,
            "weight": CONTRACT_HEALTH_WEIGHTS["payment"],
            "missing": payment_missing,
        },
        {
            "key": "contract",
            "name": CONTRACT_DIMENSION_NAMES["contract"],
            "score": margin_score,
            "weight": CONTRACT_HEALTH_WEIGHTS["contract"],
            "missing": margin_missing,
        },
        {
            "key": "guarantor",
            "name": CONTRACT_DIMENSION_NAMES["guarantor"],
            "score": guarantor_score,
            "weight": CONTRACT_HEALTH_WEIGHTS["guarantor"],
            "missing": guarantor_missing,
        },
    ]
    total = round(sum(d["score"] * d["weight"] for d in dimensions) / 100.0, 1)

    return {
        "subject_type": "CONTRACT",
        "subject_id": stage.contract_no,
        "subject_label": stage.project_name,
        "score": total,
        "grade": grade_of(total),
        "dimensions": dimensions,
        "drivers": _contract_drivers(stage, payment_score, margin_rate, worst_guarantor),
        "trend": [],
        "computed_at": _now_iso(),
    }


def _planned_payment_score(planned_date: str | None, today: date) -> tuple[float, bool]:
    """计划回款：已逾期记 10~60 线性，临近（30 天内）记 70，90 天内记 85，否则 100。"""

    if not planned_date:
        return _MISSING_NEUTRAL, True
    try:
        planned = date.fromisoformat(planned_date[:10])
    except ValueError:
        return _MISSING_NEUTRAL, True
    days = (planned - today).days
    if days < 0:
        return round(max(10.0, 60.0 + days * 1.5), 1), False
    if days <= 30:
        return 70.0, False
    if days <= 90:
        return 85.0, False
    return 100.0, False


def _contract_drivers(
    stage: SimulatedProjectStage,
    payment_score: float,
    margin_rate: float | None,
    worst_guarantor: str | None,
) -> dict[str, list[str]]:
    down: list[str] = []
    up: list[str] = []
    if stage.stage in ("回款", "验收"):
        down.append(f"项目处于{stage.stage}阶段")
    if stage.milestone_progress < 30:
        down.append(f"里程碑进度滞后（{stage.milestone_progress}%）")
    elif stage.milestone_progress >= 80:
        up.append(f"里程碑进度良好（{stage.milestone_progress}%）")
    if payment_score < 60:
        down.append("计划回款已逾期或临近")
    else:
        up.append("计划回款未逾期")
    if margin_rate is not None and margin_rate < 0:
        down.append(f"合同毛利为负（{margin_rate:.1%}）")
    if worst_guarantor is not None and worst_guarantor != "正常":
        down.append(f"担保人状态异常（{worst_guarantor}）")
    return {"down": down, "up": up}

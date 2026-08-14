"""项目类服务与事前评估。

项目视图合并真实合同（contracts，金额按元→万元）与模拟阶段/担保人（sim 数据）；
事前评估只针对模拟新项目（sim.new_projects），使用确定性规则 + 模拟数据，不调用模型。
force_review 仅标记强制人工审批，不自动改动任何名单。
"""

from __future__ import annotations

from datetime import UTC, datetime

from ict_agent.data import DuckDBStore
from ict_agent.simdata import PROJECT_AMOUNT_TIERS, SimulatedData, SimulatedGuarantor

# 事前评估结论严重度排序（数值越大越严重，只升不降）
_CONCLUSION_ORDER = {
    "正常通过": 0,
    "有条件通过": 1,
    "需要人工复核": 2,
    "暂缓项目": 3,
    "不建议通过": 4,
}

# 历史风险：超期率阈值与现有风险规则 A1（0.70）保持一致；0.30 起提示有条件通过
_HIGH_OVERDUE_RATE = 0.70
_MEDIUM_OVERDUE_RATE = 0.30


def amount_tier(amount_wan: float) -> str:
    """项目金额档位：<300 / 300~500 / 500~700 / >=700（单位：万元）。"""

    for label, threshold in PROJECT_AMOUNT_TIERS:
        if threshold == 0:
            return label
        if amount_wan < threshold:
            return label
    return ">=700"


def _escalate(current: str, target: str) -> str:
    """只向更严重的结论升级，绝不降级。"""

    if _CONCLUSION_ORDER[target] > _CONCLUSION_ORDER.get(current, 0):
        return target
    return current


def _find_guarantor(
    sim: SimulatedData,
    *,
    customer_name: str = "",
    customer_id: str = "",
    related_project: str = "",
) -> SimulatedGuarantor | None:
    """按担保人关联键匹配：优先 关联项目编号，其次 客户编号 / 客户名称。"""

    for guarantor in sim.guarantors:
        if related_project and guarantor.related_project == related_project:
            return guarantor
    for guarantor in sim.guarantors:
        if customer_id and guarantor.customer_id == customer_id:
            return guarantor
    for guarantor in sim.guarantors:
        if customer_name and guarantor.customer_name == customer_name:
            return guarantor
    return None


def _guarantor_label(guarantor: SimulatedGuarantor | None) -> str:
    """担保人展示文本：正常时只给名称，异常时标注状态。"""

    if guarantor is None:
        return ""
    if guarantor.guarantor_status in ("正常", ""):
        return guarantor.guarantor_name
    return f"{guarantor.guarantor_name}（{guarantor.guarantor_status}）"


def _fetch_payment_by_contract(store: DuckDBStore) -> dict[str, float]:
    """按合同号聚合回款金额（元），只统计 contracts 中存在的项目合同。"""

    result = store.fetch(
        'SELECT p."合同号", SUM(p."回款金额") FROM payments p '
        'INNER JOIN contracts c ON p."合同号" = c."合同编号" '
        'WHERE p."合同号" IS NOT NULL AND p."合同号" <> \'\' GROUP BY 1'
    )
    return {str(row[0]): float(row[1] or 0.0) for row in result.rows}


def _fetch_ar_by_contract(store: DuckDBStore) -> dict[str, dict[str, float]]:
    """按合同号聚合最新期末应收与超期，只统计 contracts 中存在的项目合同。"""

    result = store.fetch(
        'WITH latest AS (SELECT MAX("快照时间") AS d FROM ar_snapshots) '
        'SELECT a."合同号", SUM(a."应收金额"), SUM(a."超期应收金额") FROM ar_snapshots a, latest '
        'INNER JOIN contracts c ON a."合同号" = c."合同编号" '
        'WHERE a."快照时间" = d AND a."合同号" IS NOT NULL AND a."合同号" <> \'\' GROUP BY 1'
    )
    return {
        str(row[0]): {"ar": float(row[1] or 0.0), "overdue": float(row[2] or 0.0)}
        for row in result.rows
    }


def _fetch_contract_metrics(store: DuckDBStore) -> dict[str, dict[str, float | int | None]]:
    """按合同号聚合毛利与账期，返回 {合同号: {margin, term_gap}}。"""

    result = store.fetch(
        'SELECT "合同编号", '
        'SUM("销售金额" * "实际净毛利率_不含税") / NULLIF(SUM("销售金额"), 0), '
        'MAX(COALESCE("实际账期", 0) - COALESCE("合同文本账期", 0)) '
        "FROM contracts GROUP BY 1"
    )
    out: dict[str, dict[str, float | int | None]] = {}
    for row in result.rows:
        margin = float(row[1]) if row[1] is not None else None
        term_gap = int(row[2]) if row[2] is not None else None
        out[str(row[0])] = {"margin_rate": margin, "term_gap_days": term_gap}
    return out


def _risk_level(flags: list[str]) -> str:
    """根据风险提示集合给出项目风险等级（确定性规则）。"""

    if any(f in flags for f in ("负毛利", "回款率低于 30%")):
        return "CRITICAL"
    if any(f in flags for f in ("应收超期率高于 50%", "回款率低于 60%")):
        return "HIGH"
    if any(f.startswith("账期超期") for f in flags) or any(
        f in flags for f in ("回款率低于 80%", "应收超期率高于 30%")
    ):
        return "MEDIUM"
    return "LOW"


def list_projects(store: DuckDBStore, sim: SimulatedData) -> list[dict[str, object]]:
    """合并真实合同与模拟阶段/担保人，输出项目视图（金额统一为万元）。

    同一合同编号可能有多行签约明细，按合同号聚合金额并去重，保证 project_id 唯一。
    同时从真实 7 表计算每个合同的风险指标（回款率/超期率/毛利/账期）并给出风险等级。
    """

    stages_by_contract = {stage.contract_no: stage for stage in sim.project_stages}
    payments_by_contract = _fetch_payment_by_contract(store)
    ar_by_contract = _fetch_ar_by_contract(store)
    contract_metrics = _fetch_contract_metrics(store)

    result = store.fetch(
        'SELECT "合同编号", "客户名称", "销售金额" FROM contracts ORDER BY "合同编号"'
    )
    aggregated: dict[str, dict[str, object]] = {}
    for row in result.rows:
        contract_no = str(row[0] or "")
        customer = str(row[1] or "")
        sales_yuan = float(row[2] or 0.0)
        if contract_no in aggregated:
            current = float(str(aggregated[contract_no]["amount_wan"]))
            aggregated[contract_no]["amount_wan"] = round(current + sales_yuan / 10000.0, 2)
            continue
        amount_wan = round(sales_yuan / 10000.0, 2)
        stage = stages_by_contract.get(contract_no)
        name = stage.project_name if stage else customer
        guarantor = _find_guarantor(
            sim,
            customer_name=customer,
            related_project=stage.project_name if stage else "",
        )

        # 真实风险指标（金额单位：元 → 万元）
        paid_yuan = payments_by_contract.get(contract_no, 0.0)
        paid_wan = round(paid_yuan / 10000.0, 2)
        raw_payment_rate = paid_yuan / sales_yuan if sales_yuan > 0 else None
        payment_rate = (
            round(min(raw_payment_rate, 1.0), 4) if raw_payment_rate is not None else None
        )
        ar_info = ar_by_contract.get(contract_no)
        overdue_rate = (
            round(ar_info["overdue"] / ar_info["ar"], 4) if ar_info and ar_info["ar"] > 0 else None
        )
        metrics = contract_metrics.get(contract_no, {})
        margin_rate = metrics.get("margin_rate")
        margin_rate = float(margin_rate) if margin_rate is not None else None
        term_gap = metrics.get("term_gap_days")
        term_gap = int(term_gap) if term_gap is not None else None

        # 风险提示与等级（确定性规则）
        flags: list[str] = []
        if margin_rate is not None and margin_rate < 0:
            flags.append("负毛利")
        if raw_payment_rate is not None and raw_payment_rate > 1.0:
            flags.append("回款额超过合同额（数据待核验）")
        if payment_rate is not None and payment_rate < 0.30:
            flags.append("回款率低于 30%")
        elif payment_rate is not None and payment_rate < 0.60:
            flags.append("回款率低于 60%")
        elif payment_rate is not None and payment_rate < 0.80:
            flags.append("回款率低于 80%")
        if overdue_rate is not None and overdue_rate > 0.50:
            flags.append("应收超期率高于 50%")
        elif overdue_rate is not None and overdue_rate > 0.30:
            flags.append("应收超期率高于 30%")
        if term_gap is not None and term_gap >= 60:
            flags.append(f"账期超期 {term_gap} 天")
        if guarantor is not None and guarantor.guarantor_status not in ("正常", ""):
            flags.append(f"担保人{guarantor.guarantor_status}")

        risk_note = "；".join(flags)
        aggregated[contract_no] = {
            "project_id": contract_no,
            "name": name,
            "customer": customer,
            "amount_wan": amount_wan,
            "amount_tier": amount_tier(amount_wan),
            "stage": stage.stage if stage else "",
            "planned_payment_date": stage.planned_payment_date if stage else "",
            "milestone_progress": stage.milestone_progress if stage else 0,
            "guarantor": _guarantor_label(guarantor),
            "paid_amount_wan": paid_wan,
            "payment_rate": payment_rate,
            "overdue_rate": overdue_rate,
            "margin_rate": margin_rate,
            "term_gap_days": term_gap,
            "risk_level": _risk_level(flags),
            "risk_note": risk_note,
            "simulated": False,
        }
    return list(aggregated.values())


def list_new_projects(sim: SimulatedData) -> list[dict[str, object]]:
    """返回全部模拟新项目（含金额档位），供事前评估入口使用。"""

    return [
        {
            "project_id": item.project_id,
            "project_name": item.project_name,
            "customer_id": item.customer_id,
            "customer_name": item.customer_name,
            "customer_list": item.customer_list,
            "project_amount_wan": item.project_amount_wan,
            "amount_tier": item.amount_tier,
            "credit_amount_wan": item.credit_amount_wan,
            "guarantor": item.guarantor,
            "applied_at": item.applied_at,
            "planned_payment_date": item.planned_payment_date,
            "note": item.note,
            "simulated": True,
        }
        for item in sim.new_projects
    ]


def _is_blacklisted(store: DuckDBStore, customer_id: str) -> bool:
    """客户授信主数据是否黑名单（黑白名单状态 = 2）。"""

    result = store.fetch(
        'SELECT "黑白名单状态" FROM customer_credit WHERE "客户编号_中台" = ? LIMIT 1',
        [customer_id],
    )
    if not result.rows:
        return False
    return int(result.rows[0][0] or 0) == 2


def _latest_overdue_rate(store: DuckDBStore, customer_id: str) -> float | None:
    """该客户最新应收快照的超期率；无记录或应收为 0 返回 None。"""

    result = store.fetch(
        """
        SELECT COALESCE(SUM("应收金额"), 0) AS ar,
               COALESCE(SUM("超期应收金额"), 0) AS overdue
        FROM ar_snapshots
        WHERE "客户编号" = ?
          AND "快照时间" = (SELECT MAX("快照时间") FROM ar_snapshots)
        """,
        [customer_id],
    )
    if not result.rows:
        return None
    ar = float(result.rows[0][0] or 0.0)
    overdue = float(result.rows[0][1] or 0.0)
    if ar <= 0:
        return None
    return overdue / ar


def run_pre_assessment(
    store: DuckDBStore,
    sim: SimulatedData,
    project_id: str,
) -> dict[str, object]:
    """对模拟新项目执行确定性事前评估（黑名单 / 金额档位 / 历史超期 / 担保人）。"""

    project = next((item for item in sim.new_projects if item.project_id == project_id), None)
    if project is None:
        raise ValueError(f"未找到模拟新项目：{project_id}")

    amount_wan = float(project.project_amount_wan)
    conclusion = "正常通过"
    reasons: list[str] = []
    force_review = False

    # 1. 黑名单拦截（模拟名单或授信主数据任一命中即拦截）
    if project.customer_list in ("黑名单", "BLACK") or _is_blacklisted(store, project.customer_id):
        conclusion = _escalate(conclusion, "不建议通过")
        reasons.append(f"客户 {project.customer_name} 当前为黑名单，禁止新增项目")
        force_review = True

    # 2. 金额档位
    if amount_wan >= 700:
        force_review = True
        reasons.append("项目金额达到 700 万元档位，强制人工审批")
        conclusion = _escalate(conclusion, "需要人工复核")
    elif amount_wan >= 500:
        reasons.append("项目金额 500-700 万元，高金额复核")
        conclusion = _escalate(conclusion, "需要人工复核")

    # 3. 历史风险（最新应收快照超期率）
    overdue_rate = _latest_overdue_rate(store, project.customer_id)
    if overdue_rate is not None:
        if overdue_rate >= _HIGH_OVERDUE_RATE:
            reasons.append(f"客户历史超期率 {overdue_rate:.0%}，需人工复核")
            conclusion = _escalate(conclusion, "需要人工复核")
        elif overdue_rate >= _MEDIUM_OVERDUE_RATE:
            reasons.append(f"客户历史超期率 {overdue_rate:.0%}，建议有条件通过")
            conclusion = _escalate(conclusion, "有条件通过")

    # 4. 担保人状态（失联 / 经营异常 / 待核验 → 降级）
    guarantor = _find_guarantor(
        sim,
        customer_id=project.customer_id,
        related_project=project.project_id,
    )
    if guarantor is not None and guarantor.guarantor_status not in ("正常", ""):
        status = guarantor.guarantor_status
        if "失联" in status or "失信" in status or "涉刑" in status:
            reasons.append(f"担保人 {guarantor.guarantor_name} {status}，建议暂缓项目")
            conclusion = _escalate(conclusion, "暂缓项目")
        elif "经营异常" in status:
            reasons.append(f"担保人 {guarantor.guarantor_name} 经营异常，需人工复核")
            conclusion = _escalate(conclusion, "需要人工复核")
        else:
            reasons.append(f"担保人 {guarantor.guarantor_name} {status}，需人工复核")
            conclusion = _escalate(conclusion, "需要人工复核")

    return {
        "project_id": project.project_id,
        "name": project.project_name,
        "customer": project.customer_name,
        "amount_wan": amount_wan,
        "amount_tier": amount_tier(amount_wan),
        "conclusion": conclusion,
        "reasons": reasons,
        "force_review": force_review,
        "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "simulated": True,
    }


__all__ = [
    "amount_tier",
    "list_new_projects",
    "list_projects",
    "run_pre_assessment",
]

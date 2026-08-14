"""名单建议 / 审批 / 审计引擎（阶段 A）。

名单值：WHITE / WATCH / BLACK / GENERAL，映射 `customer_credit.黑白名单状态`：
0=GENERAL（一般）、1=WHITE（白名单）、2=BLACK（黑名单）、3=WATCH（观察中）。

规则只生成建议并记录审计，不直接修改 `customer_credit` 主数据；名单变更由人工
审批后由外部动作决定是否真正改主数据（阶段 A 先落在独立库 `list_recommendations`
与 `list_changes` 中）。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from ict_agent.data import CaseStore, DuckDBStore, ListChangeWrite, ListRecommendationWrite

logger = logging.getLogger(__name__)

# customer_credit.黑白名单状态 -> 名单值
LIST_MAP: dict[int, str] = {0: "GENERAL", 1: "WHITE", 2: "BLACK", 3: "WATCH"}
_LIST_LABELS: dict[str, str] = {
    "GENERAL": "一般",
    "WHITE": "白名单",
    "WATCH": "观察中",
    "BLACK": "黑名单",
}

# 健康度等级 -> 建议目标名单（阶段 A 演示版规则）
# 黑名单不在自动升级范围内（黑名单由人工直接管理）。
_GRADE_TO_TARGET: dict[str, str] = {
    "WARNING": "WATCH",
    "HIGH_RISK": "WATCH",
}

# 触发规则代码
TRIGGER_HEALTH_WARNING = "HEALTH_GRADE_WARNING"
TRIGGER_HEALTH_HIGH_RISK = "HEALTH_GRADE_HIGH_RISK"
TRIGGER_HEALTH_RECOVERED = "HEALTH_GRADE_RECOVERED"


def list_label(value: str) -> str:
    """名单值转中文。"""

    return _LIST_LABELS.get(value, value)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def current_list_from_credit(store: DuckDBStore) -> dict[str, str]:
    """读取 customer_credit 当前名单，返回 {客户编号: 名单值}。"""

    result = store.fetch(
        'SELECT "客户编号_中台", "黑白名单状态" FROM customer_credit ORDER BY "客户编号_中台"'
    )
    mapping: dict[str, str] = {}
    for row in result.rows:
        customer_id = str(row[0])
        status = row[1]
        if isinstance(status, bool):
            status_int = int(status)
        elif status is None:
            status_int = 0
        else:
            try:
                status_int = int(status)
            except (TypeError, ValueError):
                status_int = 0
        mapping[customer_id] = LIST_MAP.get(status_int, "GENERAL")
    return mapping


def _recommendation_dict(record: ListRecommendationWrite) -> dict[str, Any]:
    """把写入模型转换为可对外返回的 dict。"""

    return {
        "recommendation_id": record.recommendation_id,
        "subject_type": record.subject_type,
        "subject_id": record.subject_id,
        "subject_label": record.subject_label,
        "current_list": record.current_list,
        "target_list": record.target_list,
        "reason": record.reason,
        "trigger_rule": record.trigger_rule,
        "evidence": json.loads(record.evidence_json) if record.evidence_json else [],
        "health_change": record.health_change,
        "risk_amount": record.risk_amount,
        "review_due_date": record.review_due_date,
        "status": record.status,
        "reviewer": record.reviewer,
        "review_reason": record.review_reason,
        "review_at": record.review_at,
        "created_at": record.created_at,
    }


def build_recommendations(
    store: CaseStore,
    health_scores: list[dict[str, Any]],
    *,
    current_list_map: dict[str, str] | None = None,
    now: str | None = None,
    review_due_days: int = 7,
) -> list[dict[str, Any]]:
    """根据健康度与当前名单生成名单调整建议（幂等写入）。

    规则：
    - 客户健康度 `WARNING` / `HIGH_RISK` 且当前名单在 WHITE / GENERAL → 建议进入 WATCH（观察中）。
    - 健康度回到 `HEALTHY` / `WATCH` 且当前名单为 WATCH / GENERAL → 建议进入 WHITE（白名单）。
    - 黑名单（BLACK）客户不参与自动升级 / 自动恢复，由人工管理。
    - 同一 subject 已有 PENDING 建议时不再重复生成（由 `save_list_recommendation` 保证幂等）。
    """

    created_at = now or _now_iso()
    due_date = (datetime.now(UTC) + timedelta(days=review_due_days)).date().isoformat()
    current_map = current_list_map or {}
    written: list[dict[str, Any]] = []

    for item in health_scores:
        subject_id = str(item["subject_id"])
        subject_label = str(item["subject_label"])
        current_list = current_map.get(subject_id, "GENERAL")
        if current_list == "BLACK":
            continue
        grade = str(item["grade"])
        business_type = str(item.get("business_type", ""))
        score = float(item.get("score", 0.0))
        drivers = item.get("drivers", {})
        risk_amount = _risk_amount(item)
        health_change = _health_change_text(item)

        target: str | None = None
        trigger: str | None = None
        reason: str = ""

        if grade in ("WARNING", "HIGH_RISK") and current_list in ("WHITE", "GENERAL"):
            target = "WATCH"
            trigger = TRIGGER_HEALTH_HIGH_RISK if grade == "HIGH_RISK" else TRIGGER_HEALTH_WARNING
            drivers_text = (
                "；".join(drivers.get("down", [])) if drivers.get("down") else "风险信号上升"
            )
            reason = (
                f"健康度 {score:.1f} 分，等级「{grade}」，"
                f"触发原因：{drivers_text}。"
                f"建议纳入观察中名单，{review_due_days} 天后复查。"
            )
        elif grade == "HEALTHY" and current_list in ("WATCH", "GENERAL"):
            # 恢复到健康水平时建议进入白名单（观察中/一般 -> 白名单）
            target = "WHITE"
            trigger = TRIGGER_HEALTH_RECOVERED
            reason = (
                f"健康度 {score:.1f} 分，等级「{grade}」，风险已缓解，"
                f"建议恢复白名单并按常规频率监控。"
            )

        if target is None or target == current_list:
            continue

        type_label = {
            "DISTRIBUTION": "分销",
            "PROJECT": "项目",
            "SERVICE_CLOUD": "服务云",
        }.get(business_type, business_type)
        evidence = [
            {
                "id": f"health_{subject_id}_{business_type}",
                "summary": f"{type_label}健康度 {score:.1f} 分 / {grade}",
            }
        ]
        record = ListRecommendationWrite(
            recommendation_id=uuid4().hex,
            subject_type="CUSTOMER",
            subject_id=subject_id,
            subject_label=subject_label,
            current_list=current_list,
            target_list=target,
            reason=reason,
            trigger_rule=trigger or "",
            evidence_json=json.dumps(evidence, ensure_ascii=False),
            health_change=health_change,
            risk_amount=risk_amount,
            review_due_date=due_date,
            status="PENDING",
            created_at=created_at,
        )
        try:
            store.save_list_recommendation(record)
            written.append(_recommendation_dict(record))
        except Exception as exc:  # pragma: no cover - 记录错误不中断整轮
            logger.warning("名单建议写入失败 subject=%s: %s", subject_id, exc)
    return written


def _risk_amount(item: dict[str, Any]) -> float:
    """从健康度输入中尽量取一个风险金额（应收敞口近似）。"""

    # 若健康度 dict 带敞口字段则直接用；否则用维度得分反向推断一个中性 0。
    amount = item.get("risk_amount")
    if isinstance(amount, (int, float)):
        return float(amount)
    return 0.0


def _health_change_text(item: dict[str, Any]) -> str:
    """健康度变化说明：优先用 trend 末两期差值。"""

    trend = item.get("trend") or []
    if len(trend) >= 2:
        try:
            last = float(trend[-1]["score"])
            prev = float(trend[-2]["score"])
            delta = round(last - prev, 1)
            if delta >= 0:
                return f"较上期 +{delta} 分"
            return f"较上期 {delta} 分"
        except (KeyError, TypeError, ValueError):
            return "—"
    return "—"


def review_recommendation(
    store: CaseStore,
    recommendation_id: str,
    *,
    decision: Literal["APPROVED", "REJECTED"],
    reviewer: str,
    reason: str,
    now: str | None = None,
) -> dict[str, Any]:
    """审批名单建议；APPROVED 且发生名单变化时写审计记录。

    建议不存在时抛 `KeyError`（调用方映射 404）；已处理时抛 `ValueError`
    （调用方映射 409）。返回处理结果 dict：{recommendation_id, status, subject_id, ...}。
    """

    changed_at = now or _now_iso()
    subject_id = store.review_list_recommendation(
        recommendation_id,
        decision=decision,
        reviewer=reviewer,
        reason=reason,
        now=changed_at,
    )
    if subject_id is None:
        # 区分“建议不存在”与“已被处理”：存在则说明已非 PENDING
        existing = store.fetch_list_recommendations(limit=500).rows
        found = any(str(row[0]) == recommendation_id for row in existing)
        if not found:
            raise KeyError(f"未找到名单建议：{recommendation_id}")
        raise ValueError(f"名单建议 {recommendation_id} 已处理，不能重复审批。")

    # 取最新建议详情以写审计
    rows = store.fetch_list_recommendations(limit=500).rows
    record: dict[str, Any] | None = None
    for row in rows:
        if str(row[0]) == recommendation_id:
            record = {
                "subject_id": str(row[2]),
                "subject_label": str(row[3]),
                "current_list": str(row[4]),
                "target_list": str(row[5]),
            }
            break
    if record is None:  # pragma: no cover
        return {
            "recommendation_id": recommendation_id,
            "status": decision,
            "subject_id": subject_id,
        }

    if decision == "APPROVED" and record["current_list"] != record["target_list"]:
        store.insert_list_change(
            ListChangeWrite(
                change_id=uuid4().hex,
                subject_id=record["subject_id"],
                subject_label=record["subject_label"],
                from_list=record["current_list"],
                to_list=record["target_list"],
                approver=reviewer,
                reason=reason,
                recommendation_id=recommendation_id,
                changed_at=changed_at,
            )
        )
    return {
        "recommendation_id": recommendation_id,
        "status": decision,
        "subject_id": subject_id,
        "current_list": record["current_list"],
        "target_list": record["target_list"],
    }

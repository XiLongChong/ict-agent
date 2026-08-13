"""模拟舆情服务：真实性核验状态机与负面舆情视图。

舆情全部来自 `data/simulated/sim_sentiments.csv`，为模拟数据；任何返回 dict 均携带
`simulated=True`，页面必须标注“模拟”。核验动作只写案件库留痕（通知 / 预警），不修改
业务 DuckDB，也不改写模拟 CSV。
"""

from __future__ import annotations

from typing import Literal

from ict_agent.data import AlertWrite, CaseStore, NotificationWrite
from ict_agent.simdata import SIMULATED_TAG, SimulatedData, SimulatedSentiment

# 真实性状态：待核验 / 已确认 / 已排除（已排除不参与负面与评分）
VerifyDecision = Literal["CONFIRMED", "EXCLUDED"]

_VERIFY_LABELS: dict[str, str] = {
    "PENDING": "待核验",
    "CONFIRMED": "已确认",
    "EXCLUDED": "已排除",
}

# 严重程度 → 预警级别（与风险案件库 severity 对齐）
_ALERT_SEVERITY: dict[str, str] = {
    "重大": "CRITICAL",
    "高": "HIGH",
    "中": "MEDIUM",
    "低": "LOW",
}

# “严重度 >= 高”才写预警
_ALERT_MIN_SEVERITY = ("重大", "高")

# 非负面事件类型（不计入负面舆情）
_POSITIVE_EVENT_TYPES = frozenset({"正面报道"})


def _sentiment_dict(sentiment: SimulatedSentiment, **extra: object) -> dict[str, object]:
    """把一条模拟舆情转成 JSON 友好 dict，并附真实性状态中文标签。"""

    return {
        "sentiment_id": sentiment.sentiment_id,
        "title": sentiment.title,
        "source": sentiment.source,
        "published_at": sentiment.published_at,
        "subject_type": sentiment.subject_type,
        "subject": sentiment.subject,
        "event_type": sentiment.event_type,
        "severity": sentiment.severity,
        "impact_amount_wan": sentiment.impact_amount_wan,
        "verify_status": sentiment.verify_status,
        "verify_label": _VERIFY_LABELS.get(sentiment.verify_status, sentiment.verify_status),
        "related_project": sentiment.related_project,
        "process_status": sentiment.process_status,
        "simulated": True,
        **extra,
    }


def list_sentiments(sim: SimulatedData) -> list[dict[str, object]]:
    """返回全部模拟舆情（含 verify_status 与中文 verify_label）。"""

    return [_sentiment_dict(item) for item in sim.sentiments]


def active_negative_sentiments(sim: SimulatedData) -> list[dict[str, object]]:
    """返回待核验 / 已确认的负面舆情（已排除与正面报道不参与）。"""

    return [
        _sentiment_dict(item)
        for item in sim.sentiments
        if item.verify_status in ("PENDING", "CONFIRMED")
        and item.event_type not in _POSITIVE_EVENT_TYPES
    ]


def verify_sentiment(
    sim: SimulatedData,
    sentiment_id: str,
    decision: VerifyDecision,
    verifier: str,
    now: str,
    store: CaseStore | None = None,
) -> dict[str, object]:
    """核验一条待核验舆情，写通知留痕；已确认且严重度>=高时另写一条预警。

    模拟数据不可变：返回更新后的舆情 dict，不修改 sim 本身。store 为 None 时只做
    纯计算（便于预览），不写任何留痕。
    """

    sentiment = next((item for item in sim.sentiments if item.sentiment_id == sentiment_id), None)
    if sentiment is None:
        raise KeyError(f"未找到舆情：{sentiment_id}")

    # 已核验状态以案件库持久化记录为准（模拟 CSV 不可变，永远 PENDING）
    if store is not None:
        existing = store.fetch_sentiment_verification(sentiment_id).rows
        if existing:
            row = existing[0]
            current = _VERIFY_LABELS.get(str(row[1]), str(row[1]))
            raise ValueError(f"舆情 {sentiment_id} 已完成核验，状态为 {current}")

    if sentiment.verify_status != "PENDING":
        current = _VERIFY_LABELS.get(sentiment.verify_status, sentiment.verify_status)
        raise ValueError(f"舆情 {sentiment_id} 已完成核验，状态为 {current}")

    label = _VERIFY_LABELS.get(decision, decision)
    message = f"舆情「{sentiment.title}」已核验为{label}，核验人：{verifier}。"
    updated = _sentiment_dict(
        sentiment,
        verify_status=decision,
        verify_label=label,
        verifier=verifier,
        verified_at=now,
    )

    if store is None:
        return updated

    store.save_notification(
        NotificationWrite(
            notification_id=f"NTF_SENT_{sentiment_id}",
            notify_type="SENTIMENT_VERIFIED",
            subject_id=sentiment.subject,
            subject_label=f"{sentiment.subject_type} {sentiment.subject}".strip(),
            message=message,
            channel="IN_APP",
            status="SENT",
            created_at=now,
        )
    )

    if decision == "CONFIRMED" and sentiment.severity in _ALERT_MIN_SEVERITY:
        store.save_alert(
            AlertWrite(
                alert_id=f"ALT_SENT_{sentiment_id}",
                alert_type="SENTIMENT",
                subject_type=sentiment.subject_type,
                subject_id=sentiment.subject,
                subject_label=f"{sentiment.subject_type} {sentiment.subject}".strip(),
                severity=_ALERT_SEVERITY.get(sentiment.severity, "MEDIUM"),
                message=message,
                risk_amount=sentiment.impact_amount_wan,
                status="OPEN",
                created_at=now,
                related_id=sentiment_id,
            )
        )

    store.save_sentiment_verification(sentiment_id, decision, verifier, now)

    return updated


__all__ = [
    "SIMULATED_TAG",
    "VerifyDecision",
    "active_negative_sentiments",
    "list_sentiments",
    "verify_sentiment",
]

"""模拟数据加载（风险预警系统演示用）。

读取 `data/simulated/` 下的 4 份 CSV（utf-8-sig），返回结构化的内置类型数据。
模拟数据独立于 7 表业务库，绝不并入业务 DuckDB；任何展示必须标注“模拟数据”。
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SIMULATED_TAG = "模拟数据"

# 项目金额档位（300/500/700 = 项目金额，万元，已确认）
PROJECT_AMOUNT_TIERS: tuple[tuple[str, int], ...] = (
    ("<300", 300),
    ("300~500", 500),
    ("500~700", 700),
    (">=700", 0),
)


@dataclass(frozen=True)
class SimulatedProjectStage:
    """真实项目合同的模拟阶段/计划回款补充。"""

    contract_no: str
    project_name: str
    customer_name: str
    project_amount_wan: float
    stage: str
    planned_payment_date: str
    milestone_progress: int
    planned_delivery_date: str


@dataclass(frozen=True)
class SimulatedGuarantor:
    """模拟担保人。"""

    guarantor_id: str
    customer_id: str
    customer_name: str
    guarantor_name: str
    guarantor_type: str
    guarantee_amount_wan: float
    guarantor_status: str
    related_project: str
    note: str


@dataclass(frozen=True)
class SimulatedSentiment:
    """模拟舆情事件。"""

    sentiment_id: str
    title: str
    source: str
    published_at: str
    subject_type: str
    subject: str
    event_type: str
    severity: str
    impact_amount_wan: float
    verify_status: str  # PENDING | CONFIRMED | EXCLUDED
    related_project: str
    process_status: str


@dataclass(frozen=True)
class SimulatedNewProject:
    """模拟新项目（事前评估用）。"""

    project_id: str
    project_name: str
    customer_id: str
    customer_name: str
    customer_list: str
    project_amount_wan: float
    amount_tier: str
    credit_amount_wan: float
    guarantor: str
    applied_at: str
    planned_payment_date: str
    note: str


@dataclass(frozen=True)
class SimulatedData:
    """全部模拟数据的内存视图。"""

    project_stages: tuple[SimulatedProjectStage, ...]
    guarantors: tuple[SimulatedGuarantor, ...]
    sentiments: tuple[SimulatedSentiment, ...]
    new_projects: tuple[SimulatedNewProject, ...]


def _read_rows(path: Path) -> list[dict[str, str]]:
    """读取 utf-8-sig CSV 为 dict 列表，统一去 BOM。"""

    if not path.is_file():
        logger.warning("模拟数据文件缺失：%s", path)
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _to_float(value: str | None) -> float:
    if value is None or str(value).strip() == "":
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _to_int(value: str | None) -> int:
    if value is None or str(value).strip() == "":
        return 0
    try:
        return int(float(value))
    except ValueError:
        return 0


def _normalize_sentiment_status(raw: str) -> str:
    value = str(raw or "").strip()
    if value in ("已确认", "CONFIRMED"):
        return "CONFIRMED"
    if value in ("已排除", "EXCLUDED"):
        return "EXCLUDED"
    return "PENDING"


def _amount_tier(amount_wan: float) -> str:
    for label, threshold in PROJECT_AMOUNT_TIERS:
        if threshold == 0:
            return label
        if amount_wan < threshold:
            return label
    return ">=700"


def load_simulated_data(simulated_dir: Path) -> SimulatedData:
    """加载全部模拟数据；缺失文件返回空元组。"""

    stages = tuple(
        SimulatedProjectStage(
            contract_no=str(row.get("合同编号", "")),
            project_name=str(row.get("项目名称", "")),
            customer_name=str(row.get("客户名称", "")),
            project_amount_wan=_to_float(row.get("项目金额_万元")),
            stage=str(row.get("项目阶段", "")),
            planned_payment_date=str(row.get("计划回款日期", "")),
            milestone_progress=_to_int(row.get("里程碑进度_%")),
            planned_delivery_date=str(row.get("计划交付日期", "")),
        )
        for row in _read_rows(simulated_dir / "sim_project_stages.csv")
    )
    guarantors = tuple(
        SimulatedGuarantor(
            guarantor_id=str(row.get("担保人ID", "")),
            customer_id=str(row.get("客户编号", "")),
            customer_name=str(row.get("客户名称", "")),
            guarantor_name=str(row.get("担保人名称", "")),
            guarantor_type=str(row.get("担保类型", "")),
            guarantee_amount_wan=_to_float(row.get("担保金额_万元")),
            guarantor_status=str(row.get("担保人状态", "")),
            related_project=str(row.get("关联合同或项目", "")),
            note=str(row.get("备注", "")),
        )
        for row in _read_rows(simulated_dir / "sim_guarantors.csv")
    )
    sentiments = tuple(
        SimulatedSentiment(
            sentiment_id=str(row.get("舆情编号", "")),
            title=str(row.get("标题", "")),
            source=str(row.get("来源", "")),
            published_at=str(row.get("发布时间", "")),
            subject_type=str(row.get("涉及主体类型", "")),
            subject=str(row.get("涉及主体", "")),
            event_type=str(row.get("事件类型", "")),
            severity=str(row.get("严重程度", "")),
            impact_amount_wan=_to_float(row.get("影响金额_万元")),
            verify_status=_normalize_sentiment_status(row.get("真实性状态") or ""),
            related_project=str(row.get("关联合同或项目", "")),
            process_status=str(row.get("处理状态", "")),
        )
        for row in _read_rows(simulated_dir / "sim_sentiments.csv")
    )
    new_projects = tuple(
        SimulatedNewProject(
            project_id=str(row.get("项目编号", "")),
            project_name=str(row.get("项目名称", "")),
            customer_id=str(row.get("客户编号", "")),
            customer_name=str(row.get("客户名称", "")),
            customer_list=str(row.get("客户名单", "")),
            project_amount_wan=_to_float(row.get("项目金额_万元")),
            amount_tier=_amount_tier(_to_float(row.get("项目金额_万元"))),
            credit_amount_wan=_to_float(row.get("授信金额_万元")),
            guarantor=str(row.get("担保人", "")),
            applied_at=str(row.get("申请日期", "")),
            planned_payment_date=str(row.get("计划回款日期", "")),
            note=str(row.get("备注", "")),
        )
        for row in _read_rows(simulated_dir / "sim_new_projects.csv")
    )
    return SimulatedData(
        project_stages=stages,
        guarantors=guarantors,
        sentiments=sentiments,
        new_projects=new_projects,
    )

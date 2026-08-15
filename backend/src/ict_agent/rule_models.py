"""规则扫描各阶段共享的领域对象。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ict_agent.data import CaseWrite, DatabaseScalar, RuleHitWrite, RuleRunWrite


@dataclass(frozen=True)
class RuleSubject:
    """规则观察到的业务主体，不代表已经创建案件。"""

    admission_key: str
    investigation_profile: str
    subject_type: str
    subject_id: str
    subject_label: str
    subject_context: Mapping[str, DatabaseScalar]
    observation_date: str
    exposure_amount: float | None = None


@dataclass(frozen=True)
class RuleHit:
    """一条原始规则命中；案件编号只能在后续组装阶段产生。"""

    rule_hit_id: str
    subject: RuleSubject
    rule_id: str
    rule_name: str
    rule_version: str
    severity: str
    exposure_amount: float
    reason: str
    metrics: Mapping[str, object]
    threshold_source: str
    sources: tuple[str, ...]
    period: str
    threshold_version: str = ""


@dataclass(frozen=True)
class RuleHitBatch:
    """规则层一次扫描的命中集合及其数据观察期。"""

    hits: tuple[RuleHit, ...]
    observation_date: str


@dataclass(frozen=True)
class AdmittedRuleGroup:
    """通过准入漏斗、等待案件组装的一组规则命中。"""

    admission_key: str
    subject: RuleSubject
    hits: tuple[RuleHit, ...]


@dataclass(frozen=True)
class AdmissionResult:
    """准入漏斗的结果，保留被拒绝命中供扫描统计和诊断使用。"""

    groups: tuple[AdmittedRuleGroup, ...]
    rejected_hits: tuple[RuleHit, ...] = ()


@dataclass(frozen=True)
class RuleScanDraft:
    """完成规则命中、准入和案件组装但尚未写入案件库的扫描结果。"""

    run: RuleRunWrite
    cases: tuple[CaseWrite, ...]
    hits: tuple[RuleHitWrite, ...]

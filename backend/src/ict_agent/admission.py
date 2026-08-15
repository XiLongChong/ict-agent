"""规则命中的准入、去重和主体分组。"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable

from ict_agent.rule_models import AdmissionResult, AdmittedRuleGroup, RuleHit


class AdmissionFunnel:
    """把原始命中筛成可组装案件的主体信号组。

    这里仅执行跨规则的入口治理，不重复实现任何业务阈值。业务规则的阈值和组合条件
    仍由规则层负责；本层只保证命中具备完整主体、去除同一规则的重复输出，并按稳定
    主体键合并多条命中。
    """

    def admit(self, hits: Iterable[RuleHit]) -> AdmissionResult:
        grouped: OrderedDict[str, list[RuleHit]] = OrderedDict()
        rejected: list[RuleHit] = []
        seen: set[tuple[str, str, str]] = set()

        for hit in hits:
            if not self._is_admissible(hit):
                rejected.append(hit)
                continue
            fingerprint = (
                hit.subject.admission_key,
                hit.rule_id,
                hit.rule_version,
            )
            if fingerprint in seen:
                rejected.append(hit)
                continue
            seen.add(fingerprint)
            grouped.setdefault(hit.subject.admission_key, []).append(hit)

        groups = tuple(
            AdmittedRuleGroup(
                admission_key=admission_key,
                subject=items[0].subject,
                hits=tuple(items),
            )
            for admission_key, items in grouped.items()
        )
        return AdmissionResult(groups=groups, rejected_hits=tuple(rejected))

    @staticmethod
    def _is_admissible(hit: RuleHit) -> bool:
        """命中缺少案件主体时拒绝，避免组装器猜测主体或案件口径。"""

        subject = hit.subject
        return bool(
            subject.admission_key
            and subject.investigation_profile
            and subject.subject_type
            and subject.subject_id
            and subject.observation_date
            and hit.rule_id
            and hit.rule_version
            and hit.period
            and hit.sources
        )

"""把准入后的信号组装为统一案件写入对象。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ict_agent.data import CaseWrite, DatabaseScalar, RuleHitWrite
from ict_agent.rule_models import AdmissionResult, AdmittedRuleGroup, RuleHit


@dataclass(frozen=True)
class CaseAssembly:
    """案件组装器输出的案件和可持久化信号。"""

    cases: tuple[CaseWrite, ...]
    hits: tuple[RuleHitWrite, ...]


class CaseAssembler:
    """将不同入口的主体信号组转换为案件，不参与规则判断或准入决策。"""

    _SEVERITY_ORDER = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}

    def assemble(
        self,
        admission: AdmissionResult,
        *,
        rule_set_version: str,
        created_at: str,
        source: str = "RULE_SCAN",
        business_type: str | None = None,
        source_snapshot_id: str = "",
        data_quality_status: str = "UNKNOWN",
        data_quality_warnings: Sequence[str] = (),
        summary: str | None = None,
    ) -> CaseAssembly:
        cases: list[CaseWrite] = []
        persisted_hits: list[RuleHitWrite] = []
        for group in admission.groups:
            case_id = self._case_id(group)
            cases.append(
                self._assemble_case(
                    group,
                    case_id=case_id,
                    rule_set_version=rule_set_version,
                    created_at=created_at,
                    source=source,
                    business_type=business_type,
                    source_snapshot_id=source_snapshot_id,
                    data_quality_status=data_quality_status,
                    data_quality_warnings=data_quality_warnings,
                    summary=summary,
                )
            )
            persisted_hits.extend(
                self._to_persisted_hit(hit, case_id=case_id) for hit in group.hits
            )
        return CaseAssembly(cases=tuple(cases), hits=tuple(persisted_hits))

    @staticmethod
    def _case_id(group: AdmittedRuleGroup) -> str:
        """只在组装层把稳定主体键映射成案件编号，保持现有案件幂等键。"""

        return group.admission_key

    def _assemble_case(
        self,
        group: AdmittedRuleGroup,
        *,
        case_id: str,
        rule_set_version: str,
        created_at: str,
        source: str,
        business_type: str | None,
        source_snapshot_id: str,
        data_quality_status: str,
        data_quality_warnings: Sequence[str],
        summary: str | None,
    ) -> CaseWrite:
        subject = group.subject
        primary_hit = min(
            group.hits,
            key=lambda hit: (
                -self._SEVERITY_ORDER.get(hit.severity, 0),
                hit.rule_id,
            ),
        )
        exposure = subject.exposure_amount
        if exposure is None:
            exposure = max((hit.exposure_amount for hit in group.hits), default=0.0)
        subject_context = self._subject_context(group)
        return CaseWrite(
            case_id=case_id,
            investigation_profile=subject.investigation_profile,
            subject_type=subject.subject_type,
            subject_id=subject.subject_id,
            subject_label=subject.subject_label,
            subject_context=subject_context,
            observation_date=subject.observation_date,
            priority=self._priority(group.hits),
            exposure_amount=exposure,
            summary=summary
            or f"主要风险：{primary_hit.rule_name}；需结合 {len(group.hits)} 条规则信号调查核实。",
            rule_hit_count=len(group.hits),
            rule_set_version=rule_set_version,
            created_at=created_at,
            source=source,
            business_type=business_type,
            source_snapshot_id=source_snapshot_id,
            data_quality_status=data_quality_status,
            data_quality_warnings=data_quality_warnings,
        )

    @staticmethod
    def _subject_context(group: AdmittedRuleGroup) -> dict[str, DatabaseScalar]:
        """保留客户案件中所有合同信号携带的合同号。"""

        context = dict(group.subject.subject_context)
        contract_numbers: list[str] = []
        for hit in group.hits:
            for key in ("contract_number", "contract_numbers"):
                value = hit.subject.subject_context.get(key)
                if value is None:
                    continue
                for contract_number in str(value).split("、"):
                    normalized = contract_number.strip()
                    if normalized and normalized not in contract_numbers:
                        contract_numbers.append(normalized)
        if contract_numbers:
            joined = "、".join(contract_numbers)
            context["contract_numbers"] = joined
            if len(contract_numbers) == 1:
                context["contract_number"] = contract_numbers[0]
        return context

    def _priority(self, hits: tuple[RuleHit, ...]) -> str:
        return max(
            (hit.severity for hit in hits),
            key=self._SEVERITY_ORDER.__getitem__,
        )

    @staticmethod
    def _to_persisted_hit(hit: RuleHit, *, case_id: str) -> RuleHitWrite:
        return RuleHitWrite(
            rule_hit_id=hit.rule_hit_id,
            case_id=case_id,
            rule_id=hit.rule_id,
            rule_name=hit.rule_name,
            rule_version=hit.rule_version,
            severity=hit.severity,
            exposure_amount=hit.exposure_amount,
            reason=hit.reason,
            metrics=hit.metrics,
            threshold_source=hit.threshold_source,
            threshold_version=hit.threshold_version,
            sources=hit.sources,
            period=hit.period,
        )

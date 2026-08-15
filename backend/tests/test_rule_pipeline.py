"""规则命中、准入和案件组装的分层契约测试。"""

from ict_agent.admission import AdmissionFunnel
from ict_agent.case_assembler import CaseAssembler
from ict_agent.rule_models import RuleHit, RuleSubject


def _subject(*, admission_key: str = "AR|C001") -> RuleSubject:
    return RuleSubject(
        admission_key=admission_key,
        investigation_profile="RECEIVABLES",
        subject_type="CUSTOMER",
        subject_id="C001",
        subject_label="C001 测试客户",
        subject_context={"customer_id": "C001"},
        observation_date="2026-07-31",
        exposure_amount=500.0,
    )


def _hit(rule_id: str, *, subject: RuleSubject | None = None, severity: str = "MEDIUM") -> RuleHit:
    return RuleHit(
        rule_hit_id=f"hit-{rule_id}",
        subject=subject or _subject(),
        rule_id=rule_id,
        rule_name=f"规则 {rule_id}",
        rule_version="2.0.0",
        severity=severity,
        exposure_amount=100.0,
        reason="测试命中",
        metrics={"metric": 1},
        threshold_source="测试阈值",
        sources=("ar_snapshots",),
        period="2026-07-31",
    )


def test_admission_funnel_deduplicates_and_groups_hits_without_case_id() -> None:
    result = AdmissionFunnel().admit((_hit("R1"), _hit("R1"), _hit("R2")))

    assert len(result.groups) == 1
    assert [hit.rule_id for hit in result.groups[0].hits] == ["R1", "R2"]
    assert len(result.rejected_hits) == 1
    assert not hasattr(result.groups[0].hits[0], "case_id")


def test_admission_funnel_rejects_missing_subject_identity() -> None:
    invalid_subject = _subject(admission_key="")

    result = AdmissionFunnel().admit((_hit("R1", subject=invalid_subject),))

    assert result.groups == ()
    assert len(result.rejected_hits) == 1


def test_case_assembler_creates_case_and_persisted_signal_associations() -> None:
    admission = AdmissionFunnel().admit(
        (_hit("R_LOW", severity="MEDIUM"), _hit("R_HIGH", severity="HIGH"))
    )

    assembly = CaseAssembler().assemble(
        admission,
        rule_set_version="2026.08-v2",
        created_at="2026-08-15T00:00:00+00:00",
    )

    assert len(assembly.cases) == 1
    case = assembly.cases[0]
    assert case.case_id == "AR|C001"
    assert case.priority == "HIGH"
    assert case.exposure_amount == 500.0
    assert case.rule_hit_count == 2
    assert case.created_at == "2026-08-15T00:00:00+00:00"
    assert {hit.case_id for hit in assembly.hits} == {"AR|C001"}

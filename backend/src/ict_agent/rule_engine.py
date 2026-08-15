"""规则扫描编排：规则命中、准入漏斗和案件组装。"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from ict_agent.admission import AdmissionFunnel
from ict_agent.case_assembler import CaseAssembler
from ict_agent.data import DuckDBStore, RuleRunWrite
from ict_agent.rule_models import RuleScanDraft
from ict_agent.rules import RULE_SET_VERSION, RuleThresholds, collect_rule_hits


def _short_id(prefix: str, value: str) -> str:
    return f"{prefix}_{sha256(value.encode('utf-8')).hexdigest()[:20]}"


def build_rule_scan(
    store: DuckDBStore,
    thresholds: RuleThresholds | None = None,
) -> RuleScanDraft:
    """按固定顺序完成命中、准入和案件组装，返回待持久化扫描草稿。"""

    created_at = datetime.now(UTC).isoformat()
    batch = collect_rule_hits(store, thresholds)
    admission = AdmissionFunnel().admit(batch.hits)
    assembly = CaseAssembler().assemble(
        admission,
        rule_set_version=RULE_SET_VERSION,
        created_at=created_at,
    )
    ar_case_count = sum(1 for case in assembly.cases if case.investigation_profile == "RECEIVABLES")
    inv_case_count = sum(1 for case in assembly.cases if case.investigation_profile == "INVENTORY")
    run_id = _short_id("run", f"{RULE_SET_VERSION}|{created_at}")
    return RuleScanDraft(
        run=RuleRunWrite(
            run_id=run_id,
            rule_set_version=RULE_SET_VERSION,
            observation_date=batch.observation_date,
            cases_detected=len(assembly.cases),
            rule_hits=len(assembly.hits),
            receivable_cases=ar_case_count,
            inventory_cases=inv_case_count,
            created_at=created_at,
        ),
        cases=assembly.cases,
        hits=assembly.hits,
    )

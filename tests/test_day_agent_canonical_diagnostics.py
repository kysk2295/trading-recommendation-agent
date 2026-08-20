from __future__ import annotations

import datetime as dt
from dataclasses import replace
from typing import Literal, assert_never

import pytest

from tests.day_agent_version_learning_support import paper_bundle
from tests.test_day_learning_report_models import _payload
from tests.test_us_day_signal_admission import _eligible_request
from tests.test_us_day_situation_projection import _inputs, _project
from trading_agent.day_learning_report_models import (
    DayDecisionDiagnostic,
    DayDecisionStage,
    InvalidDayLearningReportError,
)
from trading_agent.day_learning_reports import (
    DayStageAssessment,
    FinalizedDayDecisionEvidence,
    build_day_decision_diagnostics,
)
from trading_agent.execution_ledger_identity import ExecutionLedgerSnapshotIdentity
from trading_agent.paper_execution_models import IntentId

type EvidenceMutation = Literal["identity", "symbol", "agent", "unreconciled"]


def test_close_diagnostics_require_finalized_paper_and_market_evidence() -> None:
    # Given: a canonical thesis, situation map, reconciled Paper bundle, and eight assessments.
    thesis = _eligible_request().thesis
    situation = _project(_inputs())
    evidence = FinalizedDayDecisionEvidence(
        thesis=thesis,
        situation=situation,
        paper=paper_bundle(thesis),
        assessed_at=dt.datetime(2026, 8, 20, 20, 6, tzinfo=dt.UTC),
        assessments=tuple(
            DayStageAssessment(stage=stage, score=0.75, reason_codes=("supported",)) for stage in DayDecisionStage
        ),
    )
    watermark = _payload().watermark.model_copy(
        update={
            "session_date": situation.session_date,
            "finalized_through": evidence.paper.snapshot.finalized_at,
        }
    )

    # When: diagnostics are built at the exact market watermark.
    diagnostics = build_day_decision_diagnostics(evidence, watermark=watermark)

    # Then: IDs are derived from typed canonical artifacts rather than caller strings.
    assert tuple(item.stage for item in diagnostics) == tuple(DayDecisionStage)
    assert all(thesis.thesis_id in item.evidence_ids for item in diagnostics)
    assert all(evidence.paper.identity.sha256 in item.evidence_ids for item in diagnostics)
    assert all("profit" not in field for field in DayDecisionDiagnostic.model_fields)


@pytest.mark.parametrize("mutation", ("identity", "symbol", "agent", "unreconciled"))
def test_close_diagnostics_reject_mismatched_or_unreconciled_canonical_evidence(
    mutation: EvidenceMutation,
) -> None:
    # Given: one canonical evidence bundle with a single trust-boundary mismatch.
    thesis = _eligible_request().thesis
    situation = _project(_inputs())
    paper = paper_bundle(thesis)
    match mutation:
        case "identity":
            changed = replace(paper, identity=ExecutionLedgerSnapshotIdentity(9, "f" * 64))
        case "symbol":
            intent = replace(paper.ledger.intents[0], symbol="MSFT")
            changed = replace(paper, ledger=replace(paper.ledger, intents=(intent,)))
        case "agent":
            intent = replace(paper.ledger.intents[0], strategy_version="other-agent")
            changed = replace(paper, ledger=replace(paper.ledger, intents=(intent,)))
        case "unreconciled":
            changed = replace(
                paper,
                ledger=replace(
                    paper.ledger,
                    unresolved_intent_ids=frozenset({IntentId(thesis.thesis_id)}),
                ),
            )
        case unreachable:
            assert_never(unreachable)
    evidence = FinalizedDayDecisionEvidence(
        thesis=thesis,
        situation=situation,
        paper=changed,
        assessed_at=changed.snapshot.finalized_at + dt.timedelta(minutes=1),
        assessments=tuple(
            DayStageAssessment(stage=stage, score=0.5, reason_codes=("reviewed",)) for stage in DayDecisionStage
        ),
    )
    watermark = _payload().watermark.model_copy(
        update={
            "session_date": situation.session_date,
            "finalized_through": changed.snapshot.finalized_at,
        }
    )

    # When / Then: canonical lineage validation fails closed.
    with pytest.raises(InvalidDayLearningReportError, match="diagnostic_evidence_invalid"):
        _ = build_day_decision_diagnostics(evidence, watermark=watermark)

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from typing import Literal, assert_never

import pytest

from tests.day_agent_version_learning_support import paper_bundle
from tests.test_day_learning_report_models import _payload
from tests.test_us_day_signal_admission import _eligible_request
from tests.test_us_day_situation_projection import _inputs, _project
from trading_agent.dashboard_paper_finalized_terminal import (
    FinalizedPaperAuthority,
    FinalizedPaperAuthorityFailure,
)
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
from trading_agent.us_day_thesis_models import ThesisChangeKind, UsDayThesisChange, UsDayTradeThesis

type EvidenceMutation = Literal["identity", "symbol", "agent", "unreconciled"]


def _changes(thesis: UsDayTradeThesis) -> tuple[UsDayThesisChange, ...]:
    return (
        UsDayThesisChange.create(
            thesis_id=thesis.thesis_id,
            parent_event_id=thesis.thesis_id,
            kind=ThesisChangeKind.CLOSE,
            occurred_at=thesis.observed_at + dt.timedelta(minutes=2),
            note="session finalized flat",
        ),
    )


def _watermark_for(evidence: FinalizedDayDecisionEvidence):
    paper = evidence.paper
    match paper.authority:
        case FinalizedPaperAuthority() as authority:
            pass
        case FinalizedPaperAuthorityFailure():
            raise AssertionError("fixture requires finalized authority")
        case unreachable:
            assert_never(unreachable)
    return _payload().watermark.model_copy(
        update={
            "session_date": evidence.situation.session_date,
            "finalized_through": paper.snapshot.finalized_at,
            "source_event_ids": tuple(
                sorted(
                    {
                        evidence.thesis.thesis_id,
                        paper.identity.sha256,
                        authority.safe_ref,
                        authority.receipt.snapshot_key,
                        authority.receipt.recovery_snapshot_sha256,
                        *(item.canonical_id for item in evidence.situation.evidence_refs),
                        *(item.canonical_id for item in evidence.thesis.evidence_refs),
                        *(str(item.intent_id) for item in paper.ledger.intents),
                        *(item.event_id for item in evidence.thesis_changes),
                    }
                )
            ),
        }
    )


def test_close_diagnostics_require_finalized_paper_and_market_evidence() -> None:
    # Given: a canonical thesis, situation map, reconciled Paper bundle, and eight assessments.
    thesis = _eligible_request().thesis
    situation = _project(_inputs())
    evidence = FinalizedDayDecisionEvidence(
        thesis=thesis,
        thesis_changes=_changes(thesis),
        situation=situation,
        paper=paper_bundle(thesis),
        assessed_at=dt.datetime(2026, 8, 20, 20, 6, tzinfo=dt.UTC),
        assessments=tuple(
            DayStageAssessment(stage=stage, score=0.75, reason_codes=("supported",)) for stage in DayDecisionStage
        ),
    )
    watermark = _watermark_for(evidence)

    # When: diagnostics are built at the exact market watermark.
    diagnostics = build_day_decision_diagnostics(evidence, watermark=watermark)

    # Then: IDs are derived from typed canonical artifacts rather than caller strings.
    assert tuple(item.stage for item in diagnostics) == tuple(DayDecisionStage)
    assert all(thesis.thesis_id in item.evidence_ids for item in diagnostics)
    assert all(evidence.paper.identity.sha256 in item.evidence_ids for item in diagnostics)
    assert all("profit" not in field for field in DayDecisionDiagnostic.model_fields)


def test_close_diagnostics_reject_caller_injected_watermark_source_id() -> None:
    # Given: otherwise canonical evidence with one caller-authored watermark ID.
    thesis = _eligible_request().thesis
    evidence = FinalizedDayDecisionEvidence(
        thesis=thesis,
        thesis_changes=_changes(thesis),
        situation=_project(_inputs()),
        paper=paper_bundle(thesis),
        assessed_at=dt.datetime(2026, 8, 20, 20, 6, tzinfo=dt.UTC),
        assessments=tuple(
            DayStageAssessment(stage=stage, score=0.75, reason_codes=("supported",)) for stage in DayDecisionStage
        ),
    )
    watermark = _watermark_for(evidence).model_copy(
        update={"source_event_ids": (*_watermark_for(evidence).source_event_ids, "9" * 64)}
    )

    # When / Then: the injected identifier is rejected rather than copied into diagnostics.
    with pytest.raises(InvalidDayLearningReportError, match="diagnostic_evidence_invalid"):
        _ = build_day_decision_diagnostics(evidence, watermark=watermark)


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
        thesis_changes=_changes(thesis),
        situation=situation,
        paper=changed,
        assessed_at=changed.snapshot.finalized_at + dt.timedelta(minutes=1),
        assessments=tuple(
            DayStageAssessment(stage=stage, score=0.5, reason_codes=("reviewed",)) for stage in DayDecisionStage
        ),
    )
    watermark = _watermark_for(evidence)

    # When / Then: canonical lineage validation fails closed.
    with pytest.raises(InvalidDayLearningReportError, match="diagnostic_evidence_invalid"):
        _ = build_day_decision_diagnostics(evidence, watermark=watermark)

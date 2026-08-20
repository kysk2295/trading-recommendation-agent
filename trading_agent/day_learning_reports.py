from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import assert_never

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.dashboard_paper_finalized_terminal import (
    FinalizedPaperAuthority,
    FinalizedPaperAuthorityFailure,
)
from trading_agent.dashboard_us_day_paper import FinalizedPaperProjectionBundle
from trading_agent.day_learning_report_models import (
    DailyLearningReport,
    DayDecisionDiagnostic,
    DayDecisionOutcome,
    DayDecisionStage,
    InvalidDayLearningReportError,
    MarketCloseReport,
    MarketCloseReportPayload,
    MarketFinalizationWatermark,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.lane_contract_keys import lane_daily_snapshot_key
from trading_agent.research_identity_models import MarketId
from trading_agent.us_day_situation_models import UsDaySituationMap
from trading_agent.us_day_thesis_models import (
    DayTradeDecision,
    ThesisChangeKind,
    UsDayThesisChange,
    UsDayTradeThesis,
    situation_id_for,
)
from trading_agent.us_equity_calendar import NEW_YORK


class DayStageAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    stage: DayDecisionStage
    score: float = Field(ge=0.0, le=1.0)
    reason_codes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_assessment(self) -> DayStageAssessment:
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise InvalidDayLearningReportError("day_diagnostic_evidence_invalid")
        return self


@dataclass(frozen=True, slots=True)
class FinalizedDayDecisionEvidence:
    thesis: UsDayTradeThesis
    thesis_changes: tuple[UsDayThesisChange, ...]
    situation: UsDaySituationMap
    paper: FinalizedPaperProjectionBundle
    assessed_at: dt.datetime
    assessments: tuple[DayStageAssessment, ...]


def build_day_decision_diagnostics(
    evidence: FinalizedDayDecisionEvidence,
    *,
    watermark: MarketFinalizationWatermark,
) -> tuple[DayDecisionDiagnostic, ...]:
    evidence_ids = _canonical_diagnostic_evidence(evidence, watermark)
    return tuple(
        DayDecisionDiagnostic(
            stage=item.stage,
            outcome=_diagnostic_outcome(item.score),
            score=item.score,
            evidence_ids=evidence_ids,
            reason_codes=item.reason_codes,
        )
        for item in evidence.assessments
    )


def _canonical_diagnostic_evidence(
    evidence: FinalizedDayDecisionEvidence,
    watermark: MarketFinalizationWatermark,
) -> tuple[str, ...]:
    thesis = UsDayTradeThesis.model_validate(evidence.thesis.model_dump(mode="python"))
    situation = UsDaySituationMap.model_validate(evidence.situation.model_dump(mode="python"))
    changes = tuple(
        UsDayThesisChange.model_validate(item.model_dump(mode="python")) for item in evidence.thesis_changes
    )
    paper = evidence.paper
    stages = tuple(item.stage for item in evidence.assessments)
    match paper.authority:
        case FinalizedPaperAuthority() as authority:
            pass
        case FinalizedPaperAuthorityFailure():
            raise InvalidDayLearningReportError("day_diagnostic_evidence_invalid") from None
        case unreachable:
            assert_never(unreachable)
    receipt = authority.receipt
    intent_matches = tuple(item for item in paper.ledger.intents if str(item.intent_id) == thesis.thesis_id)
    intent = intent_matches[0] if len(intent_matches) == 1 else None
    intent_created_at = None if intent is None else dt.datetime.fromisoformat(intent.created_at)
    situation_evidence = tuple(item.canonical_id for item in situation.evidence_refs)
    thesis_evidence = tuple(item.canonical_id for item in thesis.evidence_refs)
    change_parents = (thesis.thesis_id, *(item.event_id for item in changes[:-1]))
    change_chain_valid = all(
        item.thesis_id == thesis.thesis_id
        and item.parent_event_id == parent_id
        and item.occurred_at <= paper.snapshot.finalized_at
        for item, parent_id in zip(changes, change_parents, strict=True)
    )
    last_change_kind = None if not changes else changes[-1].kind
    match last_change_kind:
        case ThesisChangeKind.CLOSE | ThesisChangeKind.CANCEL_ENTRY | ThesisChangeKind.INVALIDATE_LOGIC:
            terminal_change = True
        case ThesisChangeKind.HOLD | ThesisChangeKind.PARTIAL_EXIT | None:
            terminal_change = False
        case unreachable:
            assert_never(unreachable)
    canonical_source_ids = tuple(
        sorted(
            {
                thesis.thesis_id,
                paper.identity.sha256,
                authority.safe_ref,
                receipt.snapshot_key,
                receipt.recovery_snapshot_sha256,
                *situation_evidence,
                *thesis_evidence,
                *(str(item.intent_id) for item in paper.ledger.intents),
                *(item.event_id for item in changes),
                *paper.ledger.pending_trade_update_receipt_keys,
                *paper.ledger.unrecovered_trade_update_quarantine_keys,
            }
        )
    )
    theme = next((item for item in situation.themes if item.theme_id == thesis.theme_id), None)
    session_date = thesis.observed_at.astimezone(NEW_YORK).date()
    paper_valid = (
        paper.identity.generation == receipt.source_ledger_generation
        and paper.identity.sha256 == receipt.source_ledger_sha256
        and paper.snapshot.source_ledger_generation == paper.identity.generation
        and paper.snapshot.source_ledger_sha256 == paper.identity.sha256
        and paper.snapshot.finalized_at == receipt.observed_at
        and paper.snapshot.session_date == receipt.session_date
        and paper.snapshot.champion_strategy_versions == receipt.strategy_versions
        and receipt.snapshot_key == str(lane_daily_snapshot_key(paper.snapshot))
        and authority.safe_ref == hashlib.sha256(receipt.model_dump_json(exclude_none=False).encode()).hexdigest()
        and paper.snapshot.data_quality_complete
        and paper.snapshot.open_order_count == 0
        and paper.snapshot.open_position_count == 0
        and not paper.ledger.unresolved_intent_ids
        and not paper.ledger.pending_trade_update_receipt_keys
        and not paper.ledger.unrecovered_trade_update_quarantine_keys
    )
    recommendation_valid = (thesis.decision is not DayTradeDecision.RECOMMEND and intent is None) or (
        thesis.decision is DayTradeDecision.RECOMMEND
        and intent is not None
        and intent_created_at is not None
        and thesis.symbol == intent.symbol
        and thesis.agent_version_id == intent.strategy_version
        and thesis.entry_price == intent.entry_limit
        and thesis.stop_price == intent.stop
        and tuple(item.price for item in thesis.targets[:2]) == (intent.target_1r, intent.target_2r)
        and thesis.observed_at <= intent_created_at <= paper.snapshot.finalized_at
        and intent.intent_id in paper.ledger.filled_intent_ids
    )
    if (
        evidence.assessed_at.tzinfo is None
        or evidence.assessed_at.utcoffset() is None
        or stages != tuple(DayDecisionStage)
        or not change_chain_valid
        or not terminal_change
        or situation_id_for(situation) != thesis.situation_id
        or thesis.agent_version_id not in receipt.strategy_versions
        or theme is None
        or thesis.catalyst_event_id not in {item.event_id for item in theme.catalysts}
        or (thesis.symbol is not None and thesis.symbol not in theme.symbols)
        or not set(thesis_evidence) <= set(situation_evidence)
        or watermark.market_id is not MarketId.US_EQUITIES
        or watermark.session_date != session_date
        or receipt.session_date != session_date
        or situation.evaluated_at > thesis.observed_at
        or evidence.assessed_at < paper.snapshot.finalized_at
        or evidence.assessed_at < watermark.finalized_through
        or watermark.source_event_ids != canonical_source_ids
        or not paper_valid
        or not recommendation_valid
    ):
        raise InvalidDayLearningReportError("day_diagnostic_evidence_invalid")
    return canonical_source_ids


def _diagnostic_outcome(score: float) -> DayDecisionOutcome:
    if score >= 0.6:
        return DayDecisionOutcome.SUPPORTED
    if score <= 0.4:
        return DayDecisionOutcome.REFUTED
    return DayDecisionOutcome.INCONCLUSIVE


def seal_market_close_report(payload: MarketCloseReportPayload) -> MarketCloseReport:
    checked = MarketCloseReportPayload.model_validate(payload.model_dump(mode="python"))
    report_id = hashlib.sha256(canonical_experiment_ledger_json(checked).encode()).hexdigest()
    return MarketCloseReport(report_id=report_id, payload=checked)


def build_daily_learning_report(
    us_report: MarketCloseReport,
    kr_report: MarketCloseReport,
    *,
    generated_at: dt.datetime,
) -> DailyLearningReport:
    checked_us = MarketCloseReport.model_validate(us_report.model_dump(mode="python"))
    checked_kr = MarketCloseReport.model_validate(kr_report.model_dump(mode="python"))
    if (
        checked_us.payload.market_id is not MarketId.US_EQUITIES
        or checked_kr.payload.market_id is not MarketId.KR_EQUITIES
    ):
        raise InvalidDayLearningReportError("day_learning_facade_market_invalid")
    return DailyLearningReport(
        us_report_id=checked_us.report_id,
        kr_report_id=checked_kr.report_id,
        generated_at=generated_at,
    )


__all__ = (
    "DayStageAssessment",
    "FinalizedDayDecisionEvidence",
    "build_daily_learning_report",
    "build_day_decision_diagnostics",
    "seal_market_close_report",
)

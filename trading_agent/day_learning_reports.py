from __future__ import annotations

import datetime as dt
import hashlib

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

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
from trading_agent.research_identity_models import MarketId


class FinalizedDayDecisionEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    agent_version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    recommendation_event_ids: tuple[str, ...]
    market_event_ids: tuple[str, ...] = Field(min_length=1)
    paper_event_ids: tuple[str, ...]
    finalized_at: AwareDatetime
    stage_scores: tuple[tuple[DayDecisionStage, float], ...]
    stage_reason_codes: tuple[tuple[DayDecisionStage, tuple[str, ...]], ...]

    @model_validator(mode="after")
    def validate_evidence(self) -> FinalizedDayDecisionEvidence:
        evidence_groups = (
            self.recommendation_event_ids,
            self.market_event_ids,
            self.paper_event_ids,
        )
        score_stages = tuple(item[0] for item in self.stage_scores)
        reason_stages = tuple(item[0] for item in self.stage_reason_codes)
        if (
            any(group != tuple(sorted(set(group))) for group in evidence_groups)
            or not self.recommendation_event_ids
            or not self.paper_event_ids
            or score_stages != tuple(DayDecisionStage)
            or reason_stages != tuple(DayDecisionStage)
        ):
            raise InvalidDayLearningReportError("day_diagnostic_evidence_invalid")
        return self


def build_day_decision_diagnostics(
    evidence: FinalizedDayDecisionEvidence,
    *,
    watermark: MarketFinalizationWatermark,
) -> tuple[DayDecisionDiagnostic, ...]:
    checked = FinalizedDayDecisionEvidence.model_validate(evidence.model_dump(mode="python"))
    if checked.finalized_at < watermark.finalized_through:
        raise InvalidDayLearningReportError("day_diagnostic_before_finalization")
    evidence_ids = tuple(
        sorted(
            set(
                (*checked.recommendation_event_ids, *checked.market_event_ids, *checked.paper_event_ids)
            )
        )
    )
    reasons = dict(checked.stage_reason_codes)
    return tuple(
        DayDecisionDiagnostic(
            stage=stage,
            outcome=_diagnostic_outcome(score),
            score=score,
            evidence_ids=evidence_ids,
            reason_codes=reasons[stage],
        )
        for stage, score in checked.stage_scores
    )


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
    "FinalizedDayDecisionEvidence",
    "build_daily_learning_report",
    "build_day_decision_diagnostics",
    "seal_market_close_report",
)

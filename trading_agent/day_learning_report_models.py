from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_types import aware

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_REASON = re.compile(r"^[a-z0-9][a-z0-9_]{0,127}$")


@dataclass(frozen=True, slots=True)
class InvalidDayLearningReportError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


class DayLearningReportModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class MarketFinalizationWatermark(DayLearningReportModel):
    watermark_id: str = Field(pattern=_SHA256_PATTERN)
    market_id: MarketId
    session_date: dt.date
    finalized_through: dt.datetime
    source_event_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_watermark(self) -> Self:
        if (
            not aware(self.finalized_through)
            or self.source_event_ids != tuple(sorted(set(self.source_event_ids)))
            or any(not event_id.strip() for event_id in self.source_event_ids)
        ):
            raise InvalidDayLearningReportError("day_report_watermark_invalid")
        return self


class ExecutionReportSection(DayLearningReportModel):
    market_id: MarketId
    actual_return: float | None
    modeled_return: float
    filled_order_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    censored_count: int = Field(ge=0)
    provider_read_only: bool
    eligibility_event_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_execution(self) -> Self:
        values = (self.modeled_return,) if self.actual_return is None else (self.actual_return, self.modeled_return)
        if not all(math.isfinite(value) for value in values):
            raise InvalidDayLearningReportError("day_report_execution_return_invalid")
        if self.eligibility_event_ids != tuple(sorted(set(self.eligibility_event_ids))):
            raise InvalidDayLearningReportError("day_report_execution_lineage_invalid")
        if self.market_id is MarketId.KR_EQUITIES and (
            not self.provider_read_only
            or self.actual_return is not None
            or self.filled_order_count != 0
            or self.eligibility_event_ids
        ):
            raise InvalidDayLearningReportError("day_report_kr_provider_read_only_required")
        if self.market_id is MarketId.US_EQUITIES and self.provider_read_only:
            raise InvalidDayLearningReportError("day_report_us_provider_mode_invalid")
        return self


class ResearchReportSection(DayLearningReportModel):
    market_id: MarketId
    attempted_variant_count: int = Field(ge=0)
    supported_count: int = Field(ge=0)
    refuted_count: int = Field(ge=0)
    inconclusive_count: int = Field(ge=0)
    modeled_return: float
    evidence_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_research(self) -> Self:
        if (
            not math.isfinite(self.modeled_return)
            or self.attempted_variant_count != self.supported_count + self.refuted_count + self.inconclusive_count
            or self.evidence_ids != tuple(sorted(set(self.evidence_ids)))
        ):
            raise InvalidDayLearningReportError("day_report_research_counts_invalid")
        return self


class DayDecisionStage(StrEnum):
    MARKET_RECOGNITION = "market_recognition"
    THEME_SELECTION = "theme_selection"
    CATALYST_INTERPRETATION = "catalyst_interpretation"
    LEADER_SELECTION = "leader_selection"
    FLOW_INTERPRETATION = "flow_interpretation"
    ENTRY = "entry"
    EXIT = "exit"
    EXECUTION_QUALITY = "execution_quality"


class DayDecisionOutcome(StrEnum):
    SUPPORTED = "supported"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"


class DayDecisionDiagnostic(DayLearningReportModel):
    stage: DayDecisionStage
    outcome: DayDecisionOutcome
    score: float = Field(ge=0.0, le=1.0)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    reason_codes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_diagnostic(self) -> Self:
        if (
            not math.isfinite(self.score)
            or self.evidence_ids != tuple(sorted(set(self.evidence_ids)))
            or any(not item.strip() for item in self.evidence_ids)
            or self.reason_codes != tuple(sorted(set(self.reason_codes)))
            or any(_REASON.fullmatch(reason) is None for reason in self.reason_codes)
        ):
            raise InvalidDayLearningReportError("day_decision_diagnostic_invalid")
        return self


class CumulativeLineageSection(DayLearningReportModel):
    market_id: MarketId
    report_count: int = Field(ge=1)
    cumulative_actual_return: float | None
    cumulative_modeled_return: float
    lineage_report_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        values = (
            (self.cumulative_modeled_return,)
            if self.cumulative_actual_return is None
            else (self.cumulative_actual_return, self.cumulative_modeled_return)
        )
        if (
            not all(math.isfinite(value) for value in values)
            or self.lineage_report_ids != tuple(dict.fromkeys(self.lineage_report_ids))
            or any(
                re.fullmatch(_SHA256_PATTERN, report_id) is None
                for report_id in self.lineage_report_ids
            )
            or self.report_count != len(self.lineage_report_ids) + 1
            or (self.market_id is MarketId.KR_EQUITIES and self.cumulative_actual_return is not None)
        ):
            raise InvalidDayLearningReportError("day_report_cumulative_lineage_invalid")
        return self


class NextSessionSection(DayLearningReportModel):
    market_id: MarketId
    active_capsule_ids: tuple[str, ...] = Field(max_length=3)
    queued_capsule_ids: tuple[str, ...]
    reason_codes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_next_session(self) -> Self:
        if (
            self.active_capsule_ids != tuple(sorted(set(self.active_capsule_ids)))
            or self.queued_capsule_ids != tuple(sorted(set(self.queued_capsule_ids)))
            or set(self.active_capsule_ids) & set(self.queued_capsule_ids)
            or any(
                re.fullmatch(_SHA256_PATTERN, capsule_id) is None
                for capsule_id in (*self.active_capsule_ids, *self.queued_capsule_ids)
            )
            or self.reason_codes != tuple(sorted(set(self.reason_codes)))
            or any(_REASON.fullmatch(reason) is None for reason in self.reason_codes)
        ):
            raise InvalidDayLearningReportError("day_report_next_session_invalid")
        return self


class MarketCloseReportPayload(DayLearningReportModel):
    market_id: MarketId
    session_date: dt.date
    watermark: MarketFinalizationWatermark
    revision: int = Field(ge=1)
    previous_report_id: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    execution: ExecutionReportSection
    research: ResearchReportSection
    lineage: CumulativeLineageSection
    next_session: NextSessionSection
    agent_version_id: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    diagnostics: tuple[DayDecisionDiagnostic, ...] = ()
    finalized_at: dt.datetime
    trading_authority: Literal[False] = False
    order_authority: Literal[False] = False
    profitability_claim: Literal[False] = False

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        sections = (self.execution, self.research, self.lineage, self.next_session)
        stages = tuple(item.stage for item in self.diagnostics)
        if (
            not aware(self.finalized_at)
            or self.finalized_at < self.watermark.finalized_through
            or self.watermark.market_id is not self.market_id
            or self.watermark.session_date != self.session_date
            or any(section.market_id is not self.market_id for section in sections)
            or (self.revision == 1) is not (self.previous_report_id is None)
            or bool(self.diagnostics) is not bool(self.agent_version_id)
            or (self.diagnostics and stages != tuple(DayDecisionStage))
        ):
            raise InvalidDayLearningReportError("day_report_revision_or_market_invalid")
        return self


class MarketCloseReport(DayLearningReportModel):
    report_id: str = Field(pattern=_SHA256_PATTERN)
    payload: MarketCloseReportPayload

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = hashlib.sha256(canonical_experiment_ledger_json(self.payload).encode()).hexdigest()
        if self.report_id != expected:
            raise InvalidDayLearningReportError("day_report_identity_invalid")
        return self


class DailyLearningReport(DayLearningReportModel):
    us_report_id: str = Field(pattern=_SHA256_PATTERN)
    kr_report_id: str = Field(pattern=_SHA256_PATTERN)
    generated_at: dt.datetime
    query_only: Literal[True] = True

    @model_validator(mode="after")
    def validate_facade(self) -> Self:
        if not aware(self.generated_at) or self.us_report_id == self.kr_report_id:
            raise InvalidDayLearningReportError("day_learning_facade_invalid")
        return self


__all__ = (
    "CumulativeLineageSection",
    "DailyLearningReport",
    "DayDecisionDiagnostic",
    "DayDecisionOutcome",
    "DayDecisionStage",
    "ExecutionReportSection",
    "InvalidDayLearningReportError",
    "MarketCloseReport",
    "MarketCloseReportPayload",
    "MarketFinalizationWatermark",
    "NextSessionSection",
    "ResearchReportSection",
)

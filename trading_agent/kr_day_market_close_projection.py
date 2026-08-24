from __future__ import annotations

import datetime as dt
import hashlib
import math
from dataclasses import dataclass
from decimal import Decimal

from trading_agent.day_learning_report_models import (
    CumulativeLineageSection,
    DayDecisionDiagnostic,
    ExecutionReportSection,
    MarketCloseReport,
    MarketCloseReportPayload,
    MarketFinalizationWatermark,
    NextSessionSection,
    ResearchReportSection,
)
from trading_agent.day_learning_reports import seal_market_close_report
from trading_agent.kr_day_capsule_outcomes import (
    KrDayCapsuleOutcome,
    KrDayCapsuleTerminalKind,
)
from trading_agent.kr_day_capsule_shadow_models import KrDayCapsuleShadowEvent
from trading_agent.kr_day_market_close_report_reader import latest_prior_kr_market_close_reports
from trading_agent.research_identity_models import MarketId


@dataclass(frozen=True, slots=True)
class KrDayMarketCloseProjectionInput:
    session_date: dt.date
    official_close_at: dt.datetime
    finalized_at: dt.datetime
    calendar_snapshot_id: str
    outcomes: tuple[KrDayCapsuleOutcome, ...]
    shadow_events: tuple[KrDayCapsuleShadowEvent, ...]
    decision_event_ids: tuple[str, ...]
    active_capsule_ids: tuple[str, ...]
    queued_capsule_ids: tuple[str, ...]
    risk_incident_ids: tuple[str, ...]
    data_incident_ids: tuple[str, ...]
    agent_version_id: str | None
    diagnostics: tuple[DayDecisionDiagnostic, ...]
    existing: tuple[MarketCloseReport, ...]
    current: MarketCloseReport | None


def build_kr_day_market_close_report(source: KrDayMarketCloseProjectionInput) -> MarketCloseReport:
    prior = latest_prior_kr_market_close_reports(source.existing, source.session_date)
    daily_return = _compound(tuple(float(item.net_return) for item in source.outcomes if item.net_return is not None))
    prior_return = 0.0 if not prior else prior[-1].payload.lineage.cumulative_modeled_return
    cumulative_return = (1.0 + prior_return) * (1.0 + daily_return) - 1.0
    source_ids = tuple(
        sorted(
            {
                source.calendar_snapshot_id,
                *(event.event_id for event in source.shadow_events),
                *source.decision_event_ids,
                *(outcome.outcome_id for outcome in source.outcomes),
                *source.risk_incident_ids,
                *source.data_incident_ids,
            }
        )
    )
    watermark_id = hashlib.sha256(
        (
            f"{source.session_date.isoformat()}:{source.official_close_at.isoformat()}:{source.calendar_snapshot_id}"
        ).encode()
    ).hexdigest()
    exits = tuple(item for item in source.outcomes if item.kind is KrDayCapsuleTerminalKind.EXIT)
    supported = sum(item.net_return is not None and item.net_return > Decimal(0) for item in exits)
    payload = MarketCloseReportPayload(
        market_id=MarketId.KR_EQUITIES,
        session_date=source.session_date,
        watermark=MarketFinalizationWatermark(
            watermark_id=watermark_id,
            market_id=MarketId.KR_EQUITIES,
            session_date=source.session_date,
            finalized_through=source.official_close_at,
            source_event_ids=source_ids,
        ),
        revision=1 if source.current is None else source.current.payload.revision + 1,
        previous_report_id=None if source.current is None else source.current.report_id,
        execution=ExecutionReportSection(
            market_id=MarketId.KR_EQUITIES,
            actual_return=None,
            modeled_return=daily_return,
            filled_order_count=0,
            unresolved_count=0,
            censored_count=sum(item.kind is KrDayCapsuleTerminalKind.CENSORED for item in source.outcomes),
            provider_read_only=True,
            eligibility_event_ids=(),
        ),
        research=ResearchReportSection(
            market_id=MarketId.KR_EQUITIES,
            attempted_variant_count=len(source.outcomes),
            supported_count=supported,
            refuted_count=len(exits) - supported,
            inconclusive_count=len(source.outcomes) - len(exits),
            modeled_return=daily_return,
            evidence_ids=source_ids,
        ),
        lineage=CumulativeLineageSection(
            market_id=MarketId.KR_EQUITIES,
            report_count=len(prior) + 1,
            cumulative_actual_return=None,
            cumulative_modeled_return=cumulative_return,
            lineage_report_ids=tuple(item.report_id for item in prior),
        ),
        next_session=NextSessionSection(
            market_id=MarketId.KR_EQUITIES,
            active_capsule_ids=source.active_capsule_ids,
            queued_capsule_ids=source.queued_capsule_ids,
            reason_codes=("kr_shadow_provider_read_only",),
        ),
        agent_version_id=source.agent_version_id,
        diagnostics=source.diagnostics,
        finalized_at=source.finalized_at,
    )
    return seal_market_close_report(payload)


def _compound(values: tuple[float, ...]) -> float:
    return math.prod(1.0 + value for value in values) - 1.0


__all__ = ("KrDayMarketCloseProjectionInput", "build_kr_day_market_close_report")

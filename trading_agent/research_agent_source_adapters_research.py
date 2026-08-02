from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import TYPE_CHECKING, final

from trading_agent.dashboard_projection_derivatives import project_derivatives
from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.lane_review_store import InvalidLaneReviewSourceError, LaneReviewReader
from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1, ResearchAgentTriggerKind
from trading_agent.research_agent_derivatives_payload import stable_derivatives_payload
from trading_agent.research_agent_source_common import (
    CapabilityEvidenceSpec,
    InvalidResearchAgentSourceError,
    ResearchAgentEvidenceMaterial,
    canonical_model_json,
    capability_evidence,
    interval_bucket,
)
from trading_agent.swing_shadow_review_store import (
    InvalidSwingShadowReviewSourceError,
    SwingShadowReviewReader,
)
from trading_agent.swing_shadow_store import InvalidSwingShadowLedgerError, SwingShadowReader

if TYPE_CHECKING:
    from trading_agent.research_agent_sources import ResearchAgentSourcePaths


@final
class SwingSourceAdapter:
    __slots__ = ()

    def collect(
        self,
        paths: ResearchAgentSourcePaths,
        now: dt.datetime,
    ) -> tuple[ResearchAgentEvidenceV1, ...]:
        if not paths.swing_shadow_database.exists() and not paths.swing_review_database.exists():
            return (
                capability_evidence(
                    CapabilityEvidenceSpec(
                        family="swing_trading",
                        source_key="swing.blocked.shadow_ledger_unavailable",
                        market_id="us_equities",
                    ),
                    now,
                ),
            )
        projected: list[ResearchAgentEvidenceV1] = []
        try:
            if paths.swing_shadow_database.exists():
                reader = SwingShadowReader(paths.swing_shadow_database)
                if not reader.is_initialized():
                    raise InvalidSwingShadowLedgerError
                signals = reader.signals()[-32:]
                for signal in signals:
                    events = reader.events(signal.signal_id)
                    payload = json.dumps(
                        {
                            "events": [event.model_dump(mode="json") for event in events],
                            "signal": signal.model_dump(mode="json"),
                        },
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    projected.append(
                        ResearchAgentEvidenceMaterial(
                            family="swing_trading",
                            trigger=ResearchAgentTriggerKind.OPEN_WORK,
                            source_key=f"swing.signal.{_safe_identity(signal.signal_id)}",
                            observed_at=max((event.observed_at for event in events), default=signal.observed_at),
                            available_at=signal.observed_at,
                            market_id=signal.strategy_lane.market_id.value,
                            canonical_payload=payload,
                        ).evidence()
                    )
            if paths.swing_review_database.exists():
                review_reader = SwingShadowReviewReader(paths.swing_review_database)
                if not review_reader.is_initialized():
                    raise InvalidSwingShadowReviewSourceError
                for stored in review_reader.events()[-32:]:
                    event = stored.event
                    projected.append(
                        ResearchAgentEvidenceMaterial(
                            family="swing_trading",
                            trigger=ResearchAgentTriggerKind.REVIEWER_FEEDBACK,
                            source_key=f"swing.review.{stored.event_key}",
                            observed_at=event.reviewed_at,
                            available_at=event.reviewed_at,
                            market_id="us_equities",
                            canonical_payload=canonical_model_json(event),
                        ).evidence()
                    )
        except (InvalidSwingShadowLedgerError, InvalidSwingShadowReviewSourceError, OSError, ValueError):
            raise InvalidResearchAgentSourceError(reason="swing_source_invalid") from None
        if projected:
            return tuple(projected)
        return (
            capability_evidence(
                CapabilityEvidenceSpec(
                    family="swing_trading",
                    source_key="swing.blocked.shadow_evidence_empty",
                    market_id="us_equities",
                ),
                now,
            ),
        )


@final
class SystematicSourceAdapter:
    __slots__ = ()

    def collect(
        self,
        paths: ResearchAgentSourcePaths,
        now: dt.datetime,
    ) -> tuple[ResearchAgentEvidenceV1, ...]:
        if not paths.experiment_ledger.exists() and not paths.lane_review_database.exists():
            return (
                capability_evidence(
                    CapabilityEvidenceSpec(
                        family="systematic_quant",
                        source_key="systematic.blocked.experiment_ledger_unavailable",
                        market_id="none",
                    ),
                    now,
                ),
            )
        projected: list[ResearchAgentEvidenceV1] = []
        try:
            if paths.experiment_ledger.exists():
                reader = ExperimentLedgerReader(paths.experiment_ledger)
                if not reader.is_initialized():
                    raise InvalidExperimentLedgerSourceError
                for stored in reader.research_sources()[-32:]:
                    source = stored.source
                    projected.append(
                        ResearchAgentEvidenceMaterial(
                            family="systematic_quant",
                            trigger=ResearchAgentTriggerKind.NEW_DATA,
                            source_key=f"systematic.source.{_safe_identity(source.source_id)}",
                            observed_at=source.ledger_recorded_at,
                            available_at=source.ledger_recorded_at,
                            market_id="none",
                            canonical_payload=canonical_model_json(source),
                        ).evidence()
                    )
                for trial in reader.trials()[-32:]:
                    for stored in reader.trial_events(trial.registration.trial_id):
                        event = stored.event
                        if event.event_kind is not TrialEventKind.STARTED:
                            projected.append(
                                ResearchAgentEvidenceMaterial(
                                    family="systematic_quant",
                                    trigger=ResearchAgentTriggerKind.EXPERIMENT_RESULT,
                                    source_key=f"systematic.trial.{_safe_identity(stored.event_key)}",
                                    observed_at=event.occurred_at,
                                    available_at=event.occurred_at,
                                    market_id="none",
                                    canonical_payload=canonical_model_json(event),
                                ).evidence()
                            )
            if paths.lane_review_database.exists():
                reviews = LaneReviewReader(paths.lane_review_database)
                if not reviews.is_initialized():
                    raise InvalidLaneReviewSourceError
                for stored in reviews.events()[-32:]:
                    projected.append(
                        ResearchAgentEvidenceMaterial(
                            family="systematic_quant",
                            trigger=ResearchAgentTriggerKind.REVIEWER_FEEDBACK,
                            source_key=f"systematic.review.{stored.event_key}",
                            observed_at=stored.event.reviewed_at,
                            available_at=stored.event.reviewed_at,
                            market_id="none",
                            canonical_payload=canonical_model_json(stored.event),
                        ).evidence()
                    )
        except (
            InvalidExperimentLedgerSourceError,
            InvalidLaneReviewSourceError,
            UnsupportedExperimentLedgerSchemaError,
            OSError,
            ValueError,
        ):
            raise InvalidResearchAgentSourceError(reason="systematic_source_invalid") from None
        if projected:
            return tuple(projected)
        return (
            capability_evidence(
                CapabilityEvidenceSpec(
                    family="systematic_quant",
                    source_key="systematic.blocked.research_evidence_empty",
                    market_id="none",
                ),
                now,
            ),
        )


@final
class DerivativesSourceAdapter:
    __slots__ = ()

    def collect(
        self,
        paths: ResearchAgentSourcePaths,
        now: dt.datetime,
    ) -> tuple[ResearchAgentEvidenceV1, ...]:
        projection = project_derivatives(paths.outputs_root, now=now)
        if projection.workspace.state in {"corrupt", "error"}:
            raise InvalidResearchAgentSourceError(reason="derivatives_source_invalid")
        blocker = projection.workspace.blocker_code
        source_key = "derivatives.snapshot" if blocker is None else f"derivatives.blocked.{blocker}"
        observed_at = projection.workspace.observed_at or interval_bucket(now, 15)
        payload = json.dumps(
            {
                "interval_observed_at": observed_at.isoformat(),
                "projection": json.loads(stable_derivatives_payload(projection)),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return (
            ResearchAgentEvidenceMaterial(
                family="derivatives_research",
                trigger=ResearchAgentTriggerKind.MARKET_EVENT,
                source_key=source_key,
                observed_at=observed_at,
                available_at=observed_at,
                market_id="us_equities",
                canonical_payload=payload,
            ).evidence(),
        )


def _safe_identity(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


__all__ = ("DerivativesSourceAdapter", "SwingSourceAdapter", "SystematicSourceAdapter")

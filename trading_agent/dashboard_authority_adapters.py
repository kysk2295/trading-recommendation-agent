from __future__ import annotations

import datetime as dt
from typing import assert_never

from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1
from trading_agent.dashboard_trigger_authority import PersistedTriggerAuthorityResolver
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.lane_review_store import (
    InvalidLaneReviewSourceError,
    LaneReviewReader,
    UnsupportedLaneReviewSchemaError,
)


class ProductionTriggerAuthorityResolver:
    def __init__(
        self,
        *,
        persisted: PersistedTriggerAuthorityResolver,
        experiments: ExperimentLedgerReader,
        reviews: LaneReviewReader,
    ) -> None:
        self._persisted = persisted
        self._experiments = experiments
        self._reviews = reviews

    def blocker(self, trigger: AutonomousTriggerV1, now: dt.datetime) -> str | None:
        match trigger.trigger_type:
            case "new_data" | "market_event" | "approved_schedule":
                return self._persisted.blocker(trigger, now)
            case "experiment_result":
                return self._experiment_blocker(trigger)
            case "reviewer_feedback":
                return self._review_blocker(trigger)
            case unreachable:
                assert_never(unreachable)

    def _experiment_blocker(self, trigger: AutonomousTriggerV1) -> str | None:
        try:
            chains = tuple(self._experiments.trial_events(trial_id) for trial_id in trigger.source_receipt_ids)
        except (InvalidExperimentLedgerSourceError, UnsupportedExperimentLedgerSchemaError):
            return "experiment_authority_invalid"
        if any(not chain for chain in chains):
            return "experiment_authority_missing"
        events = tuple(chain[-1] for chain in chains)
        keys = tuple(str(stored.event_key) for stored in events)
        occurred = tuple(stored.event.occurred_at for stored in events)
        if (
            keys != trigger.evidence_refs
            or trigger.payload_sha256 != keys[-1]
            or len(set(occurred)) != 1
            or trigger.observed_at != occurred[0]
            or trigger.authorized_at != occurred[0]
        ):
            return "experiment_authority_mismatch"
        return None

    def _review_blocker(self, trigger: AutonomousTriggerV1) -> str | None:
        try:
            persisted = self._reviews.events()
            events = tuple(
                next(
                    (stored for stored in persisted if stored.event.snapshot_key == source_id),
                    None,
                )
                for source_id in trigger.source_receipt_ids
            )
        except (InvalidLaneReviewSourceError, UnsupportedLaneReviewSchemaError):
            return "reviewer_authority_invalid"
        if any(stored is None for stored in events):
            return "reviewer_authority_missing"
        verified = tuple(stored for stored in events if stored is not None)
        keys = tuple(str(stored.event_key) for stored in verified)
        reviewed = tuple(stored.event.reviewed_at for stored in verified)
        if (
            keys != trigger.evidence_refs
            or trigger.payload_sha256 != keys[-1]
            or len(set(reviewed)) != 1
            or trigger.observed_at != reviewed[0]
            or trigger.authorized_at != reviewed[0]
        ):
            return "reviewer_authority_mismatch"
        return None


__all__ = ("ProductionTriggerAuthorityResolver",)

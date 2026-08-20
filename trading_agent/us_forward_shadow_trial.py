from __future__ import annotations

import datetime as dt
import hashlib
import json

from pydantic import BaseModel

from trading_agent.day_forward_trial_identity import (
    DayForwardExitReason,
    DayForwardTrialEventKind,
    ForwardExecutionLane,
)
from trading_agent.day_forward_trial_models import (
    DayForwardOutcomeRef,
    DayForwardTrial,
    DayForwardTrialEvent,
)
from trading_agent.day_strategy_capsule_models import StrategyCapsule
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.research_identity_models import MarketId
from trading_agent.us_forward_shadow_artifacts import (
    UsForwardShadowOutcomeArtifact,
    artifact_sha256,
)
from trading_agent.us_forward_shadow_models import UsForwardShadowTick


def build_us_forward_shadow_trial(
    capsule: StrategyCapsule,
    source_refs: tuple[str, ...],
    tick: UsForwardShadowTick,
    *,
    evaluation_at: dt.datetime,
) -> DayForwardTrial:
    registration_completed_bar_at = completed_bar_at(tick)
    interval = completed_bar_interval(tick)
    payload = {
        "schema_version": 1,
        "trial_id": "",
        "capsule_id": capsule.capsule_id,
        "hypothesis_version_id": capsule.hypothesis_version_id,
        "market_id": MarketId.US_EQUITIES,
        "execution_lane": ForwardExecutionLane.FORWARD_PROBE,
        "session_id": tick.session_id,
        "session_date": tick.session_date,
        "calendar_snapshot_id": tick.calendar_snapshot_id,
        "cost_model_sha256": _model_sha256(capsule.cost_model),
        "source_refs_sha256": _model_sha256(source_refs),
        "evidence_schema_sha256": _model_sha256(capsule.evidence_schema),
        "preregistered_at": evaluation_at,
        "registration_completed_bar_at": registration_completed_bar_at,
        "first_eligible_completed_bar_at": registration_completed_bar_at + interval,
        "trading_authority": False,
        "profitability_claim": False,
    }
    return DayForwardTrial.model_validate(
        payload | {"trial_id": DayForwardTrial.canonical_id_for(payload)}
    )


def build_us_forward_shadow_event(
    trial: DayForwardTrial,
    tick: UsForwardShadowTick,
    kind: DayForwardTrialEventKind,
    *,
    sequence: int,
    previous_event_id: str | None,
    exit_reason: DayForwardExitReason | None = None,
    outcome_ref: DayForwardOutcomeRef | None = None,
    reason_codes: tuple[str, ...] = (),
) -> DayForwardTrialEvent:
    payload = {
        "schema_version": 1,
        "event_id": "",
        "trial_id": trial.trial_id,
        "market_id": trial.market_id,
        "session_id": trial.session_id,
        "session_date": trial.session_date,
        "sequence": sequence,
        "previous_event_id": previous_event_id,
        "event_kind": kind,
        "completed_bar_id": tick.completed_bar_id,
        "completed_bar_sequence": tick.completed_bar_sequence,
        "completed_bar_at": completed_bar_at(tick),
        "event_at": completed_bar_at(tick),
        "exit_reason": exit_reason,
        "outcome_ref": outcome_ref,
        "reason_codes": tuple(sorted(set(reason_codes))),
        "trading_authority": False,
        "profitability_claim": False,
    }
    return DayForwardTrialEvent.model_validate(
        payload | {"event_id": DayForwardTrialEvent.canonical_id_for(payload)}
    )


def build_us_forward_shadow_outcome_ref(
    outcome: UsForwardShadowOutcomeArtifact,
) -> DayForwardOutcomeRef:
    digest = artifact_sha256(outcome)
    payload = {
        "schema_version": 1,
        "outcome_id": "",
        "artifact_ref": f"artifact://safe/{digest}",
        "artifact_sha256": digest,
        "recorded_at": outcome.recorded_at,
        "profitability_claim": False,
    }
    return DayForwardOutcomeRef.model_validate(
        payload | {"outcome_id": DayForwardOutcomeRef.canonical_id_for(payload)}
    )


def completed_bar_interval(tick: UsForwardShadowTick) -> dt.timedelta:
    if len(tick.bars) < 2:
        return dt.timedelta(minutes=1)
    return tick.bars[-1].timestamp - tick.bars[-2].timestamp


def completed_bar_at(tick: UsForwardShadowTick) -> dt.datetime:
    return tick.bars[-1].timestamp + dt.timedelta(minutes=1)


def _model_sha256(value: BaseModel | tuple[str, ...]) -> str:
    if isinstance(value, BaseModel):
        encoded = canonical_experiment_ledger_json(value)
    else:
        encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


__all__ = (
    "build_us_forward_shadow_event",
    "build_us_forward_shadow_outcome_ref",
    "build_us_forward_shadow_trial",
    "completed_bar_at",
    "completed_bar_interval",
)

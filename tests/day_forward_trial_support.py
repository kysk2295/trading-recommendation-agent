from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from tests.test_day_strategy_capsule_store import _prepared_store
from trading_agent.day_forward_trial_models import (
    DayForwardExitReason,
    DayForwardOutcomeRef,
    DayForwardTrial,
    DayForwardTrialEvent,
    DayForwardTrialEventKind,
    ForwardExecutionLane,
)
from trading_agent.day_strategy_capsule import publish_day_strategy_capsule
from trading_agent.day_strategy_capsule_models import StrategyCapsule
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.research_identity_models import MarketId

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def prepared_trial(
    path: Path,
) -> tuple[ExperimentLedgerStore, StrategyCapsule, DayForwardTrial]:
    store, request = _prepared_store(path)
    capsule, _ = publish_day_strategy_capsule(store, request)
    stored_version = store.day_hypothesis_version(capsule.hypothesis_version_id)
    assert stored_version is not None
    return store, capsule, trial_for_capsule(capsule, stored_version.version.source_refs)


def trial_for_capsule(
    capsule: StrategyCapsule,
    source_refs: tuple[str, ...],
    *,
    market_id: MarketId | None = None,
) -> DayForwardTrial:
    selected_market = capsule.market_id if market_id is None else market_id
    registration_bar = capsule.published_at
    first_eligible = registration_bar + dt.timedelta(minutes=1)
    exchange = "XNYS" if selected_market is MarketId.US_EQUITIES else "XKRX"
    timezone = ZoneInfo("America/New_York" if selected_market is MarketId.US_EQUITIES else "Asia/Seoul")
    payload = {
        "schema_version": 1,
        "trial_id": "",
        "capsule_id": capsule.capsule_id,
        "hypothesis_version_id": capsule.hypothesis_version_id,
        "market_id": selected_market,
        "execution_lane": ForwardExecutionLane.FORWARD_PROBE,
        "session_id": f"{exchange}-{first_eligible.astimezone(timezone).date().isoformat()}",
        "session_date": first_eligible.astimezone(timezone).date(),
        "calendar_snapshot_id": f"calendar://official/{exchange}/2026-v1",
        "cost_model_sha256": _sha_json(capsule.cost_model.model_dump(mode="json")),
        "source_refs_sha256": _sha_json(list(source_refs)),
        "evidence_schema_sha256": _sha_json(list(capsule.evidence_schema)),
        "preregistered_at": registration_bar + dt.timedelta(seconds=30),
        "registration_completed_bar_at": registration_bar,
        "first_eligible_completed_bar_at": first_eligible,
        "trading_authority": False,
        "profitability_claim": False,
    }
    return DayForwardTrial.model_validate(
        payload | {"trial_id": DayForwardTrial.canonical_id_for(payload)}
    )


def arbitrary_trial(
    index: int,
    market_id: MarketId = MarketId.US_EQUITIES,
) -> DayForwardTrial:
    timezone = ZoneInfo("America/New_York" if market_id is MarketId.US_EQUITIES else "Asia/Seoul")
    exchange = "XNYS" if market_id is MarketId.US_EQUITIES else "XKRX"
    registration_bar = dt.datetime(2026, 8, 20, 14 if market_id is MarketId.US_EQUITIES else 1, tzinfo=dt.UTC)
    first_eligible = registration_bar + dt.timedelta(minutes=1)
    payload = {
        "schema_version": 1,
        "trial_id": "",
        "capsule_id": f"{index + 1:064x}",
        "hypothesis_version_id": f"{index + 101:064x}",
        "market_id": market_id,
        "execution_lane": ForwardExecutionLane.FORWARD_PROBE,
        "session_id": f"{exchange}-{first_eligible.astimezone(timezone).date().isoformat()}",
        "session_date": first_eligible.astimezone(timezone).date(),
        "calendar_snapshot_id": f"calendar://official/{exchange}/2026-v1",
        "cost_model_sha256": SHA_A,
        "source_refs_sha256": SHA_B,
        "evidence_schema_sha256": SHA_C,
        "preregistered_at": registration_bar + dt.timedelta(seconds=30),
        "registration_completed_bar_at": registration_bar,
        "first_eligible_completed_bar_at": first_eligible,
        "trading_authority": False,
        "profitability_claim": False,
    }
    return DayForwardTrial.model_validate(
        payload | {"trial_id": DayForwardTrial.canonical_id_for(payload)}
    )


def outcome_ref(recorded_at: dt.datetime) -> DayForwardOutcomeRef:
    payload = {
        "schema_version": 1,
        "outcome_id": "",
        "artifact_ref": f"artifact://safe/{SHA_C}",
        "artifact_sha256": SHA_C,
        "recorded_at": recorded_at,
        "profitability_claim": False,
    }
    return DayForwardOutcomeRef.model_validate(
        payload | {"outcome_id": DayForwardOutcomeRef.canonical_id_for(payload)}
    )


def trial_event(
    trial: DayForwardTrial,
    kind: DayForwardTrialEventKind,
    sequence: int,
    bar_sequence: int,
    *,
    previous_event_id: str | None = None,
    completed_bar_at: dt.datetime | None = None,
    event_at: dt.datetime | None = None,
) -> DayForwardTrialEvent:
    bar_at = (
        trial.first_eligible_completed_bar_at + dt.timedelta(minutes=bar_sequence - 1)
        if completed_bar_at is None
        else completed_bar_at
    )
    observed_at = bar_at + dt.timedelta(seconds=5) if event_at is None else event_at
    terminal_outcome = outcome_ref(observed_at) if kind is DayForwardTrialEventKind.EXIT else None
    reasons = (
        ("fixture_reason",)
        if kind
        in {
            DayForwardTrialEventKind.BLOCKED,
            DayForwardTrialEventKind.FAILED,
            DayForwardTrialEventKind.CENSORED,
        }
        else ()
    )
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
        "completed_bar_id": hashlib.sha256(
            f"{trial.session_id}:{bar_sequence}".encode()
        ).hexdigest(),
        "completed_bar_sequence": bar_sequence,
        "completed_bar_at": bar_at,
        "event_at": observed_at,
        "exit_reason": (
            DayForwardExitReason.STOP if kind is DayForwardTrialEventKind.EXIT else None
        ),
        "outcome_ref": terminal_outcome,
        "reason_codes": reasons,
        "trading_authority": False,
        "profitability_claim": False,
    }
    return DayForwardTrialEvent.model_validate(
        payload | {"event_id": DayForwardTrialEvent.canonical_id_for(payload)}
    )


def _sha_json(value: dict[str, str] | list[str]) -> str:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()

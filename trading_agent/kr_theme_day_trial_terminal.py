from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Final, override
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_models import ExperimentTrialEvent, TrialEventKind
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerStore,
    ExperimentLedgerWriterLeaseUnavailableError,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.kr_theme_day_shadow_entry_models import KrThemeDayShadowEntry
from trading_agent.kr_theme_day_shadow_entry_store import (
    InvalidKrThemeDayShadowEntryStoreError,
    KrThemeDayShadowEntryStore,
)
from trading_agent.kr_theme_day_shadow_exit_models import KrThemeDayShadowExit
from trading_agent.kr_theme_day_shadow_exit_store import (
    InvalidKrThemeDayShadowExitStoreError,
    KrThemeDayShadowExitStore,
)
from trading_agent.kr_theme_day_trial import InvalidKrThemeDayTrialError, require_exact_kr_theme_day_trial
from trading_agent.kr_theme_day_trial_terminal_models import (
    KrThemeDayTrialTerminalArtifact,
    KrThemeDayTrialTerminalPayload,
    KrThemeDayTrialTerminalReason,
    KrThemeDayTrialTerminalRequest,
    kr_theme_day_trial_terminal_artifact,
)
from trading_agent.kr_theme_day_trial_terminal_store import (
    InvalidKrThemeDayTrialTerminalStoreError,
    KrThemeDayTrialTerminalStore,
)
from trading_agent.multi_market_trial_keys import multi_market_trial_registration_key
from trading_agent.multi_market_trial_models import MultiMarketExperimentTrialRegistration

_KST: Final = ZoneInfo("Asia/Seoul")
_SESSION_CLOSE: Final = dt.time(15, 30)


class InvalidKrThemeDayTrialTerminalError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day trial terminal input is invalid"


@dataclass(frozen=True, slots=True)
class KrThemeDayTrialTerminalStores:
    entry_store: KrThemeDayShadowEntryStore
    exit_store: KrThemeDayShadowExitStore
    terminal_store: KrThemeDayTrialTerminalStore


@dataclass(frozen=True, slots=True)
class KrThemeDayTrialTerminalResult:
    artifact_created: bool
    event_created: bool
    artifact: KrThemeDayTrialTerminalArtifact
    event: ExperimentTrialEvent


@dataclass(frozen=True, slots=True)
class _TerminalEvidence:
    kind: TrialEventKind
    reasons: tuple[str, ...]
    entries: tuple[KrThemeDayShadowEntry, ...]
    exits: tuple[KrThemeDayShadowExit, ...]


@dataclass(frozen=True, slots=True)
class _ExpectedLineage:
    trial: MultiMarketExperimentTrialRegistration
    trial_registration_key: str
    started_event_key: str


def finalize_kr_theme_day_shadow_trial(
    ledger: ExperimentLedgerStore,
    stores: KrThemeDayTrialTerminalStores,
    request: KrThemeDayTrialTerminalRequest,
) -> KrThemeDayTrialTerminalResult:
    try:
        request = KrThemeDayTrialTerminalRequest.model_validate(request.model_dump(mode="python"))
        trial = _trial(ledger, request.trial_id)
        _require_terminal_time(trial, request.occurred_at)
        events = ledger.multi_market_trial_events(request.trial_id)
        if len(events) not in (1, 2) or events[0].event.event_kind is not TrialEventKind.STARTED:
            raise InvalidKrThemeDayTrialTerminalError
        expected = _ExpectedLineage(
            trial,
            str(multi_market_trial_registration_key(trial)),
            str(events[0].event_key),
        )
        evidence = _evidence(stores, expected)
        payload = KrThemeDayTrialTerminalPayload(
            trial_id=trial.trial_id,
            strategy_version=trial.strategy_version,
            session_date=trial.planned_start,
            started_event_key=events[0].event_key,
            terminal_kind=evidence.kind,
            reason_codes=evidence.reasons,
            entry_ids=tuple(entry.entry_id for entry in evidence.entries),
            entry_payload_sha256s=tuple(_payload_sha256(entry) for entry in evidence.entries),
            exit_ids=tuple(exit.exit_id for exit in evidence.exits),
            exit_payload_sha256s=tuple(_payload_sha256(exit) for exit in evidence.exits),
            terminal_at=request.occurred_at,
        )
        artifact = kr_theme_day_trial_terminal_artifact(payload)
        artifact_created = stores.terminal_store.append(artifact)
        event = ExperimentTrialEvent(
            trial_id=trial.trial_id,
            sequence=2,
            event_kind=evidence.kind,
            occurred_at=request.occurred_at,
            artifact_sha256s=(artifact.artifact_id,),
            reason_codes=evidence.reasons,
            previous_event_key=events[0].event_key,
        )
        with ledger.writer() as writer:
            event_created = writer.append_multi_market_trial_event(event)
    except (
        AttributeError,
        ExperimentLedgerConflictError,
        ExperimentLedgerWriterLeaseUnavailableError,
        InvalidExperimentLedgerSourceError,
        InvalidKrThemeDayTrialError,
        InvalidKrThemeDayTrialTerminalStoreError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrThemeDayTrialTerminalError from None
    return KrThemeDayTrialTerminalResult(artifact_created, event_created, artifact, event)


def _trial(ledger: ExperimentLedgerStore, trial_id: str) -> MultiMarketExperimentTrialRegistration:
    matches = tuple(
        stored.registration for stored in ledger.multi_market_trials() if stored.registration.trial_id == trial_id
    )
    if len(matches) != 1:
        raise InvalidKrThemeDayTrialTerminalError
    require_exact_kr_theme_day_trial(ledger, matches[0])
    return matches[0]


def _require_terminal_time(trial: MultiMarketExperimentTrialRegistration, occurred_at: dt.datetime) -> None:
    local = occurred_at.astimezone(_KST)
    if local.date() != trial.planned_start or local.time() < _SESSION_CLOSE:
        raise InvalidKrThemeDayTrialTerminalError


def _evidence(
    stores: KrThemeDayTrialTerminalStores,
    expected: _ExpectedLineage,
) -> _TerminalEvidence:
    reasons: list[str] = []
    try:
        all_entries = stores.entry_store.entries()
    except InvalidKrThemeDayShadowEntryStoreError:
        reasons.append(KrThemeDayTrialTerminalReason.SHADOW_ENTRY_STORE_INVALID.value)
        all_entries = ()
    try:
        all_exits = stores.exit_store.exits()
    except InvalidKrThemeDayShadowExitStoreError:
        reasons.append(KrThemeDayTrialTerminalReason.SHADOW_EXIT_STORE_INVALID.value)
        all_exits = ()
    if reasons:
        return _TerminalEvidence(TrialEventKind.FAILED, tuple(sorted(reasons)), (), ())
    trial = expected.trial
    entries = tuple(
        sorted((item for item in all_entries if item.trial_id == trial.trial_id), key=lambda item: item.entry_id)
    )
    exits = tuple(
        sorted((item for item in all_exits if item.trial_id == trial.trial_id), key=lambda item: item.exit_id)
    )
    if not _lineage_matches(expected, entries, exits):
        return _TerminalEvidence(
            TrialEventKind.FAILED,
            (KrThemeDayTrialTerminalReason.SHADOW_ARTIFACT_LINEAGE_MISMATCH.value,),
            entries,
            exits,
        )
    if not entries:
        return _TerminalEvidence(
            TrialEventKind.CENSORED,
            (KrThemeDayTrialTerminalReason.NO_SHADOW_ENTRY_ARTIFACT.value,),
            entries,
            exits,
        )
    if len(entries) != len(exits):
        return _TerminalEvidence(
            TrialEventKind.CENSORED,
            (KrThemeDayTrialTerminalReason.INCOMPLETE_SHADOW_EXIT_PATH.value,),
            entries,
            exits,
        )
    return _TerminalEvidence(TrialEventKind.COMPLETED, (), entries, exits)


def _lineage_matches(
    expected: _ExpectedLineage,
    entries: tuple[KrThemeDayShadowEntry, ...],
    exits: tuple[KrThemeDayShadowExit, ...],
) -> bool:
    trial = expected.trial
    entries_by_id = {entry.entry_id: entry for entry in entries}
    if any(
        entry.strategy_version != trial.strategy_version
        or entry.trial_registration_key != expected.trial_registration_key
        or entry.started_event_key != expected.started_event_key
        for entry in entries
    ):
        return False
    for exit in exits:
        entry = entries_by_id.get(exit.entry_id)
        if (
            entry is None
            or exit.strategy_version != trial.strategy_version
            or exit.signal_id != entry.signal_id
            or exit.symbol != entry.symbol
            or exit.entry_fill_price != entry.fill_price
            or exit.stop_price != entry.stop_price
            or exit.first_target_price != entry.target_prices[0]
        ):
            return False
    return len({exit.entry_id for exit in exits}) == len(exits)


def _payload_sha256(value: KrThemeDayShadowEntry | KrThemeDayShadowExit) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(value).encode()).hexdigest()

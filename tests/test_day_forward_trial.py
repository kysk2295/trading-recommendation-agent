from __future__ import annotations

import datetime as dt
import importlib
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.day_forward_trial_support import (
    arbitrary_trial,
    prepared_trial,
    trial_event,
    trial_for_capsule,
)
from trading_agent.day_forward_trial_ledger import (
    DayForwardTrialLedgerConflictError,
    InvalidDayForwardTrialLedgerSourceError,
    append_day_forward_trial_event,
    read_day_forward_trial_state,
    register_day_forward_trial,
)
from trading_agent.day_forward_trial_models import (
    DayForwardTrial,
    DayForwardTrialEventKind,
)
from trading_agent.day_research_ledger_reader import (
    day_forward_trial as read_day_forward_trial,
)
from trading_agent.day_research_ledger_reader import (
    day_forward_trials as read_day_forward_trials,
)
from trading_agent.research_identity_models import MarketId


def test_forward_trial_public_contract_exists() -> None:
    # Given: the shared Day foundation package.
    module = importlib.import_module("trading_agent.day_forward_trial_models")

    # When: the Forward Trial contract is inspected.
    names = {
        "DayForwardOutcomeRef",
        "DayForwardTrial",
        "DayForwardTrialEvent",
        "DayForwardTrialEventKind",
        "ForwardExecutionLane",
    }

    # Then: every authority-free public model is available.
    assert names <= set(module.__all__)


def test_forward_trial_ledger_public_contract_exists() -> None:
    # Given: an append-only Day experiment ledger.
    module = importlib.import_module("trading_agent.day_forward_trial_ledger")

    # When: the restart-safe trial API is inspected.
    names = {
        "append_day_forward_trial_event",
        "day_forward_trials",
        "read_day_forward_trial_state",
        "register_day_forward_trial",
    }

    # Then: registration, replay, append, and read operations are available.
    assert names <= set(module.__all__)


def test_first_eligible_bar_must_follow_registration_bar() -> None:
    # Given: a canonical trial registration payload.
    trial = arbitrary_trial(1)
    payload = trial.model_dump(mode="python") | {
        "trial_id": "",
        "first_eligible_completed_bar_at": trial.registration_completed_bar_at,
    }

    # When/Then: the registration bar is rejected as forward evidence.
    with pytest.raises(ValidationError, match="forward_trial_not_future"):
        DayForwardTrial.model_validate(
            payload | {"trial_id": DayForwardTrial.canonical_id_for(payload)}
        )


@pytest.mark.parametrize("kind", tuple(DayForwardTrialEventKind))
def test_every_forward_event_kind_has_a_canonical_model(
    kind: DayForwardTrialEventKind,
) -> None:
    # Given: a future-only trial and one event kind.
    trial = arbitrary_trial(2)

    # When: the event is materialized.
    event = trial_event(trial, kind, 1, 1)

    # Then: its immutable identity includes that exact kind.
    assert event.event_kind is kind
    assert event.event_id == event.canonical_id_for(event.model_dump(mode="python"))


def test_trial_registration_replays_and_reads_after_restart(tmp_path: Path) -> None:
    # Given: a persisted capsule with an exact same-market version and declarations.
    store, _capsule, trial = prepared_trial(tmp_path / "ledger.sqlite3")

    # When: registration is appended twice and the database is reopened.
    with sqlite3.connect(store.path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert register_day_forward_trial(connection, trial) is True
        assert register_day_forward_trial(connection, trial) is False
        connection.commit()
    with sqlite3.connect(store.path) as restarted:
        state = read_day_forward_trial_state(restarted, trial.trial_id)
        projected = read_day_forward_trial(restarted, trial.trial_id)
        market_trials = read_day_forward_trials(restarted, trial.market_id)

    # Then: exact replay yields one immutable open trial.
    assert state.trial == trial
    assert state.events == ()
    assert state.terminal is False
    assert projected == state
    assert market_trials == (state,)


def test_trial_registration_rejects_cross_market_capsule(tmp_path: Path) -> None:
    # Given: a US capsule wrapped in an otherwise valid KR trial.
    store, capsule, _trial = prepared_trial(tmp_path / "ledger.sqlite3")
    stored_version = store.day_hypothesis_version(capsule.hypothesis_version_id)
    assert stored_version is not None
    cross_market = trial_for_capsule(
        capsule,
        stored_version.version.source_refs,
        market_id=MarketId.KR_EQUITIES,
    )

    # When/Then: parent coherence fails before insertion.
    with (
        sqlite3.connect(store.path) as connection,
        pytest.raises(
            InvalidDayForwardTrialLedgerSourceError,
            match="forward_trial_parent_mismatch",
        ),
    ):
        _ = register_day_forward_trial(connection, cross_market)


@pytest.mark.parametrize(
    "field",
    ("cost_model_sha256", "source_refs_sha256", "evidence_schema_sha256"),
)
def test_trial_registration_rejects_declaration_hash_mismatch(
    tmp_path: Path,
    field: str,
) -> None:
    # Given: a trial whose capsule/version declaration commitment was changed.
    store, _capsule, trial = prepared_trial(tmp_path / "ledger.sqlite3")
    payload = trial.model_dump(mode="python") | {"trial_id": "", field: "d" * 64}
    changed = DayForwardTrial.model_validate(
        payload | {"trial_id": DayForwardTrial.canonical_id_for(payload)}
    )

    # When/Then: parent coherence rejects the changed commitment before insertion.
    with (
        sqlite3.connect(store.path) as connection,
        pytest.raises(
            InvalidDayForwardTrialLedgerSourceError,
            match="forward_trial_parent_mismatch",
        ),
    ):
        _ = register_day_forward_trial(connection, changed)


def test_event_chain_replays_exactly_and_conflicts_on_changed_content(
    tmp_path: Path,
) -> None:
    # Given: a registered trial and its first signal event.
    store, _capsule, trial = prepared_trial(tmp_path / "ledger.sqlite3")
    signal = trial_event(trial, DayForwardTrialEventKind.SIGNAL, 1, 1)
    changed = trial_event(
        trial,
        DayForwardTrialEventKind.SIGNAL,
        1,
        1,
        event_at=signal.event_at + dt.timedelta(seconds=1),
    )

    # When: the exact event is retried before changed content for the same bar/kind.
    with sqlite3.connect(store.path) as connection:
        assert register_day_forward_trial(connection, trial)
        assert append_day_forward_trial_event(connection, signal) == signal
        assert append_day_forward_trial_event(connection, signal) == signal
        with pytest.raises(
            DayForwardTrialLedgerConflictError,
            match="forward_trial_event_identity_conflict",
        ):
            _ = append_day_forward_trial_event(connection, changed)

    # Then: only the original immutable event remains.
    with sqlite3.connect(store.path) as connection:
        assert read_day_forward_trial_state(connection, trial.trial_id).events == (signal,)


def test_signal_entry_exit_chain_survives_restart(tmp_path: Path) -> None:
    # Given: a registered trial and a valid signal-entry-exit sequence.
    store, _capsule, trial = prepared_trial(tmp_path / "ledger.sqlite3")
    signal = trial_event(trial, DayForwardTrialEventKind.SIGNAL, 1, 1)
    entry = trial_event(
        trial,
        DayForwardTrialEventKind.ENTRY,
        2,
        1,
        previous_event_id=signal.event_id,
    )
    exit_event = trial_event(
        trial,
        DayForwardTrialEventKind.EXIT,
        3,
        2,
        previous_event_id=entry.event_id,
    )

    # When: the chain is appended and read through a new SQLite connection.
    with sqlite3.connect(store.path) as connection:
        assert register_day_forward_trial(connection, trial)
        assert append_day_forward_trial_event(connection, signal) == signal
        assert append_day_forward_trial_event(connection, entry) == entry
        assert append_day_forward_trial_event(connection, exit_event) == exit_event
        connection.commit()
    with sqlite3.connect(store.path) as restarted:
        state = read_day_forward_trial_state(restarted, trial.trial_id)

    # Then: replay derives the terminal state from the exact event chain.
    assert state.events == (signal, entry, exit_event)
    assert state.terminal is True
    assert state.outcome_refs == (exit_event.outcome_ref,)


def test_completed_bar_gap_is_censored_without_inferred_signal(tmp_path: Path) -> None:
    # Given: one observed bar followed by a proposal that skips a canonical bar.
    store, _capsule, trial = prepared_trial(tmp_path / "ledger.sqlite3")
    no_signal = trial_event(trial, DayForwardTrialEventKind.NO_SIGNAL, 1, 1)
    skipped_signal = trial_event(
        trial,
        DayForwardTrialEventKind.SIGNAL,
        2,
        3,
        previous_event_id=no_signal.event_id,
    )

    # When: the skipped-bar proposal is appended and retried after restart.
    with sqlite3.connect(store.path) as connection:
        assert register_day_forward_trial(connection, trial)
        assert append_day_forward_trial_event(connection, no_signal) == no_signal
        censored = append_day_forward_trial_event(connection, skipped_signal)
        connection.commit()
    with sqlite3.connect(store.path) as restarted:
        replayed = append_day_forward_trial_event(restarted, skipped_signal)
        state = read_day_forward_trial_state(restarted, trial.trial_id)

    # Then: the host records one deterministic terminal censor, never the proposal.
    assert censored.event_kind is DayForwardTrialEventKind.CENSORED
    assert censored.reason_codes == ("completed_bar_gap",)
    assert replayed == censored
    assert state.events == (no_signal, censored)
    assert state.terminal is True


def test_event_before_first_eligible_bar_is_rejected(tmp_path: Path) -> None:
    # Given: a registered trial and a backdated no-signal observation.
    store, _capsule, trial = prepared_trial(tmp_path / "ledger.sqlite3")
    backdated = trial_event(
        trial,
        DayForwardTrialEventKind.NO_SIGNAL,
        1,
        1,
        completed_bar_at=trial.registration_completed_bar_at,
    )

    # When/Then: the ledger rejects it as non-forward evidence.
    with sqlite3.connect(store.path) as connection:
        assert register_day_forward_trial(connection, trial)
        with pytest.raises(
            InvalidDayForwardTrialLedgerSourceError,
            match="forward_trial_event_not_future",
        ):
            _ = append_day_forward_trial_event(connection, backdated)


def test_reader_fails_closed_on_noncanonical_event_payload(tmp_path: Path) -> None:
    # Given: a stored event whose append-only trigger is removed for corruption simulation.
    store, _capsule, trial = prepared_trial(tmp_path / "ledger.sqlite3")
    event = trial_event(trial, DayForwardTrialEventKind.NO_SIGNAL, 1, 1)
    with sqlite3.connect(store.path) as connection:
        assert register_day_forward_trial(connection, trial)
        assert append_day_forward_trial_event(connection, event) == event
        connection.execute("DROP TRIGGER day_forward_trial_events_no_update")
        connection.execute(
            "UPDATE day_forward_trial_events SET payload_json=payload_json || ' '",
        )
        connection.commit()

    # When/Then: restart replay rejects the noncanonical immutable payload.
    with (
        sqlite3.connect(store.path) as restarted,
        pytest.raises(
            InvalidDayForwardTrialLedgerSourceError,
            match="stored_forward_trial_event_index_invalid",
        ),
    ):
        _ = read_day_forward_trial_state(restarted, trial.trial_id)

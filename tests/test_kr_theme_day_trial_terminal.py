from __future__ import annotations

import datetime as dt
import sqlite3
import stat
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.test_kr_theme_day_shadow_entry import OBSERVED, _ledger, _signal
from tests.test_kr_theme_day_shadow_exit import FIRST_BAR, _bar
from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.kr_theme_day_shadow_entry import project_kr_theme_day_shadow_entry
from trading_agent.kr_theme_day_shadow_entry_store import KrThemeDayShadowEntryStore
from trading_agent.kr_theme_day_shadow_exit import project_kr_theme_day_shadow_exit
from trading_agent.kr_theme_day_shadow_exit_store import KrThemeDayShadowExitStore
from trading_agent.kr_theme_day_trial_terminal import (
    InvalidKrThemeDayTrialTerminalError,
    KrThemeDayTrialTerminalStores,
    finalize_kr_theme_day_shadow_trial,
)
from trading_agent.kr_theme_day_trial_terminal_models import (
    KrThemeDayTrialTerminalReason,
    KrThemeDayTrialTerminalRequest,
)
from trading_agent.kr_theme_day_trial_terminal_store import (
    InvalidKrThemeDayTrialTerminalStoreError,
    KrThemeDayTrialTerminalStore,
)

KST = ZoneInfo("Asia/Seoul")
CLOSED_AT = dt.datetime(2026, 7, 20, 15, 31, tzinfo=KST)


def _trial_stores(
    tmp_path: Path,
    *,
    with_entry: bool = True,
    with_exit: bool = True,
) -> tuple[
    KrThemeDayTrialTerminalStores,
    str,
]:
    ledger = _ledger(tmp_path / "experiment.sqlite3")
    trial_id = ledger.multi_market_trials()[0].registration.trial_id
    entries = KrThemeDayShadowEntryStore(tmp_path / "entries.sqlite3")
    exits = KrThemeDayShadowExitStore(tmp_path / "exits.sqlite3")
    terminals = KrThemeDayTrialTerminalStore(tmp_path / "terminals.sqlite3")
    if not with_entry:
        return KrThemeDayTrialTerminalStores(entries, exits, terminals), trial_id
    entry = project_kr_theme_day_shadow_entry(
        ledger,
        entries,
        _signal(),
        filled_at=OBSERVED + dt.timedelta(seconds=1),
    ).entry
    if with_exit:
        bar = _bar(FIRST_BAR, high="10300")
        result = project_kr_theme_day_shadow_exit(
            entries,
            exits,
            entry.entry_id,
            (bar,),
            evaluated_at=bar.observed_at,
        )
        assert result is not None
    return KrThemeDayTrialTerminalStores(entries, exits, terminals), trial_id


def _request(trial_id: str, occurred_at: dt.datetime = CLOSED_AT) -> KrThemeDayTrialTerminalRequest:
    return KrThemeDayTrialTerminalRequest(trial_id=trial_id, occurred_at=occurred_at)


def test_complete_entry_exit_pair_closes_trial_and_replays(tmp_path: Path) -> None:
    stores, trial_id = _trial_stores(tmp_path)
    ledger = _ledger(tmp_path / "experiment.sqlite3")

    first = finalize_kr_theme_day_shadow_trial(ledger, stores, _request(trial_id))
    replay = finalize_kr_theme_day_shadow_trial(ledger, stores, _request(trial_id))

    assert first.artifact_created is True
    assert first.event_created is True
    assert replay.artifact_created is False
    assert replay.event_created is False
    assert first.event.event_kind is TrialEventKind.COMPLETED
    assert first.event.artifact_sha256s == (first.artifact.artifact_id,)
    assert len(first.artifact.payload.entry_payload_sha256s) == 1
    assert len(first.artifact.payload.exit_payload_sha256s) == 1
    assert stat.S_IMODE(stores.terminal_store.path.stat().st_mode) == 0o600
    assert len(ledger.multi_market_trial_events(trial_id)) == 2


def test_empty_entry_day_is_censored_instead_of_zero_return(tmp_path: Path) -> None:
    stores, trial_id = _trial_stores(tmp_path, with_entry=False)
    ledger = _ledger(tmp_path / "experiment.sqlite3")

    result = finalize_kr_theme_day_shadow_trial(ledger, stores, _request(trial_id))

    assert result.event.event_kind is TrialEventKind.CENSORED
    assert result.event.reason_codes == (KrThemeDayTrialTerminalReason.NO_SHADOW_ENTRY_ARTIFACT.value,)
    assert result.artifact.payload.entry_ids == ()
    assert result.artifact.payload.exit_ids == ()


def test_missing_exit_is_censored_as_incomplete_path(tmp_path: Path) -> None:
    stores, trial_id = _trial_stores(tmp_path, with_exit=False)
    ledger = _ledger(tmp_path / "experiment.sqlite3")

    result = finalize_kr_theme_day_shadow_trial(ledger, stores, _request(trial_id))

    assert result.event.event_kind is TrialEventKind.CENSORED
    assert result.event.reason_codes == (KrThemeDayTrialTerminalReason.INCOMPLETE_SHADOW_EXIT_PATH.value,)
    assert len(result.artifact.payload.entry_ids) == 1
    assert result.artifact.payload.exit_ids == ()


def test_tampered_entry_store_fails_trial_without_claiming_performance(tmp_path: Path) -> None:
    stores, trial_id = _trial_stores(tmp_path)
    ledger = _ledger(tmp_path / "experiment.sqlite3")
    with sqlite3.connect(stores.entry_store.path) as connection:
        _ = connection.execute("DROP TRIGGER kr_theme_day_shadow_entries_no_update")
        connection.commit()

    result = finalize_kr_theme_day_shadow_trial(ledger, stores, _request(trial_id))

    assert result.event.event_kind is TrialEventKind.FAILED
    assert result.event.reason_codes == (KrThemeDayTrialTerminalReason.SHADOW_ENTRY_STORE_INVALID.value,)
    assert result.artifact.payload.entry_ids == ()
    assert result.artifact.payload.exit_ids == ()


def test_valid_but_wrong_started_event_lineage_fails_trial(tmp_path: Path) -> None:
    stores, trial_id = _trial_stores(tmp_path)
    ledger = _ledger(tmp_path / "experiment.sqlite3")
    original = stores.entry_store.entries()[0]
    forged_entry = type(original).model_validate(original.model_dump(mode="python") | {"started_event_key": "0" * 64})
    forged_store = KrThemeDayShadowEntryStore(tmp_path / "forged-entries.sqlite3")
    assert forged_store.append(forged_entry) is True
    forged_stores = KrThemeDayTrialTerminalStores(
        forged_store,
        stores.exit_store,
        stores.terminal_store,
    )

    result = finalize_kr_theme_day_shadow_trial(ledger, forged_stores, _request(trial_id))

    assert result.event.event_kind is TrialEventKind.FAILED
    assert result.event.reason_codes == (KrThemeDayTrialTerminalReason.SHADOW_ARTIFACT_LINEAGE_MISMATCH.value,)


def test_terminal_requires_session_close_and_private_untampered_store(tmp_path: Path) -> None:
    stores, trial_id = _trial_stores(tmp_path)
    ledger = _ledger(tmp_path / "experiment.sqlite3")
    before_close = dt.datetime(2026, 7, 20, 15, 29, tzinfo=KST)

    with pytest.raises(InvalidKrThemeDayTrialTerminalError):
        _ = finalize_kr_theme_day_shadow_trial(ledger, stores, _request(trial_id, before_close))
    assert not stores.terminal_store.path.exists()

    _ = finalize_kr_theme_day_shadow_trial(ledger, stores, _request(trial_id))
    with sqlite3.connect(stores.terminal_store.path) as connection:
        _ = connection.execute("DROP TRIGGER kr_theme_day_trial_terminals_no_update")
        connection.commit()
    with pytest.raises(InvalidKrThemeDayTrialTerminalStoreError):
        _ = stores.terminal_store.artifacts()

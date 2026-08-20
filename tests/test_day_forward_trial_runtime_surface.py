from __future__ import annotations

from pathlib import Path

from tests.day_forward_trial_support import prepared_trial, trial_event
from trading_agent.day_forward_trial_models import DayForwardTrialEventKind
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    ExperimentLedgerWriter,
)
from trading_agent.research_identity_models import MarketId


def test_experiment_ledger_facade_exposes_forward_trial_and_policy_operations() -> None:
    # Given: the single experiment-ledger reader and writer authority types.
    reader_names = {"day_exploration_policies", "day_forward_trials"}
    writer_names = {"append_day_forward_trial_event", "register_day_forward_trial"}

    # When: their public Day Forward Shadow surface is inspected.
    reader_surface = set(dir(ExperimentLedgerReader))
    writer_surface = set(dir(ExperimentLedgerWriter))

    # Then: the runtime never needs a second SQLite writer or private connection access.
    assert reader_names <= reader_surface
    assert writer_names <= writer_surface


def test_entered_trial_records_observed_bar_before_later_exit(
    tmp_path: Path,
) -> None:
    # Given: an entered trial that remains open for one completed bar.
    store, _capsule, trial = prepared_trial(tmp_path / "ledger.sqlite3")
    observed_kind = DayForwardTrialEventKind("observed")
    signal = trial_event(trial, DayForwardTrialEventKind.SIGNAL, 1, 1)
    entry = trial_event(
        trial,
        DayForwardTrialEventKind.ENTRY,
        2,
        1,
        previous_event_id=signal.event_id,
    )
    observed = trial_event(
        trial,
        observed_kind,
        3,
        2,
        previous_event_id=entry.event_id,
    )
    exit_event = trial_event(
        trial,
        DayForwardTrialEventKind.EXIT,
        4,
        3,
        previous_event_id=observed.event_id,
    )

    # When: every transition is appended through the authority facade and reopened.
    with store.writer() as writer:
        assert writer.register_day_forward_trial(trial)
        assert writer.append_day_forward_trial_event(signal) == signal
        assert writer.append_day_forward_trial_event(entry) == entry
        assert writer.append_day_forward_trial_event(observed) == observed
        assert writer.append_day_forward_trial_event(exit_event) == exit_event
    state = store.reader().day_forward_trials(MarketId.US_EQUITIES)[0]

    # Then: the open-position observation preserves contiguous bar evidence.
    assert state.events == (signal, entry, observed, exit_event)
    assert state.terminal is True

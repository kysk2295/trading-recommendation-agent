from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_kr_theme_day_shadow_entry import _signal
from tests.test_kr_theme_day_trial_terminal import CLOSED_AT, _request, _trial_stores
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.hermes_delivery_models import HermesDeliveryKind, hermes_delivery_id
from trading_agent.hermes_delivery_projection import project_trade_signals
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_theme_day_terminal_delivery import (
    InvalidKrThemeDayTerminalDeliveryError,
    KrThemeDayTerminalDeliverySources,
    project_kr_theme_day_terminal_delivery,
)
from trading_agent.kr_theme_day_terminal_delivery_state import (
    kr_theme_day_terminal_delivery_references,
)
from trading_agent.kr_theme_day_trial_terminal import KrThemeDayTrialTerminalStores, finalize_kr_theme_day_shadow_trial


def _sources(tmp_path: Path) -> KrThemeDayTerminalDeliverySources:
    stores, _ = _trial_stores(tmp_path)
    return KrThemeDayTerminalDeliverySources(
        entry_store=stores.entry_store,
        exit_store=stores.exit_store,
        terminal_store=stores.terminal_store,
        delivery_store=HermesDeliveryStore(tmp_path / "delivery.sqlite3"),
    )


def _project_actionable(sources: KrThemeDayTerminalDeliverySources) -> None:
    with sources.delivery_store.writer() as writer:
        result = project_trade_signals((_signal(),), writer, frozenset())
    assert result.inserted == 1


def test_completed_shadow_exit_replies_to_actionable_and_exactly_replays(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    trial_id = sources.entry_store.entries()[0].trial_id
    _project_actionable(sources)
    terminal = finalize_kr_theme_day_shadow_trial(
        ExperimentLedgerStore(tmp_path / "experiment.sqlite3"),
        KrThemeDayTrialTerminalStores(sources.entry_store, sources.exit_store, sources.terminal_store),
        _request(trial_id),
    )

    first = project_kr_theme_day_terminal_delivery(sources, trial_id)
    replay = project_kr_theme_day_terminal_delivery(sources, trial_id)

    assert (first.inserted, first.replayed) == (1, 0)
    assert (replay.inserted, replay.replayed) == (0, 1)
    actionable, exit_event = sources.delivery_store.events()
    shadow_exit = sources.exit_store.exits()[0]
    assert exit_event.kind is HermesDeliveryKind.EXIT
    assert exit_event.root_delivery_id == actionable.delivery_id
    assert exit_event.root_delivery_id == hermes_delivery_id(shadow_exit.signal_id, actionable.contract_version)
    assert exit_event.occurred_at == shadow_exit.exit_at
    assert exit_event.status == shadow_exit.reason.value
    assert f"terminal:{terminal.artifact.artifact_id}" in exit_event.evidence_refs
    assert "shadow" in exit_event.rendered_text
    assert "주문" in exit_event.rendered_text
    assert kr_theme_day_terminal_delivery_references(sources.delivery_store, terminal.artifact) == (
        f"delivery:{exit_event.delivery_id}",
    )


def test_no_entry_terminal_emits_one_no_recommendation(tmp_path: Path) -> None:
    stores, trial_id = _trial_stores(tmp_path, with_entry=False)
    sources = KrThemeDayTerminalDeliverySources(
        entry_store=stores.entry_store,
        exit_store=stores.exit_store,
        terminal_store=stores.terminal_store,
        delivery_store=HermesDeliveryStore(tmp_path / "delivery.sqlite3"),
    )
    terminal = finalize_kr_theme_day_shadow_trial(
        ExperimentLedgerStore(tmp_path / "experiment.sqlite3"),
        stores,
        _request(trial_id),
    )

    result = project_kr_theme_day_terminal_delivery(sources, trial_id)

    assert result.inserted == 1
    event = sources.delivery_store.events()[0]
    assert event.kind is HermesDeliveryKind.NO_RECOMMENDATION
    assert event.root_delivery_id == event.delivery_id
    assert event.occurred_at == CLOSED_AT
    assert event.status == "no_shadow_entry_artifact"
    assert event.evidence_refs == (f"terminal:{terminal.artifact.artifact_id}",)
    assert "추천 없음" in event.rendered_text


def test_incomplete_exit_emits_incident_reply_to_actionable(tmp_path: Path) -> None:
    stores, trial_id = _trial_stores(tmp_path, with_exit=False)
    sources = KrThemeDayTerminalDeliverySources(
        entry_store=stores.entry_store,
        exit_store=stores.exit_store,
        terminal_store=stores.terminal_store,
        delivery_store=HermesDeliveryStore(tmp_path / "delivery.sqlite3"),
    )
    _project_actionable(sources)
    _ = finalize_kr_theme_day_shadow_trial(
        ExperimentLedgerStore(tmp_path / "experiment.sqlite3"),
        stores,
        _request(trial_id),
    )

    result = project_kr_theme_day_terminal_delivery(sources, trial_id)

    assert result.inserted == 1
    actionable, incident = sources.delivery_store.events()
    assert incident.kind is HermesDeliveryKind.INCIDENT
    assert incident.root_delivery_id == actionable.delivery_id
    assert incident.status == "incomplete_shadow_exit_path"
    assert "불완전" in incident.rendered_text


def test_exit_without_original_actionable_fails_closed(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    trial_id = sources.entry_store.entries()[0].trial_id
    _ = finalize_kr_theme_day_shadow_trial(
        ExperimentLedgerStore(tmp_path / "experiment.sqlite3"),
        KrThemeDayTrialTerminalStores(sources.entry_store, sources.exit_store, sources.terminal_store),
        _request(trial_id),
    )

    with pytest.raises(InvalidKrThemeDayTerminalDeliveryError):
        _ = project_kr_theme_day_terminal_delivery(sources, trial_id)

    assert sources.delivery_store.events() == ()

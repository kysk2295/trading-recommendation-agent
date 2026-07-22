from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import run_us_systematic_regime as cli
import trading_agent.systematic_regime_operating as operating
from tests.test_systematic_regime_engine import _source
from tests.test_systematic_regime_trial import CODE_VERSION, _extend_source
from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.swing_shadow_models import SwingDailySource
from trading_agent.systematic_regime_operating import (
    SystematicOperatingPhase,
    run_systematic_regime_tick,
)
from trading_agent.systematic_regime_schema import SYSTEMATIC_REGIME_SCHEMA_V1
from trading_agent.systematic_regime_store import (
    SystematicRegimeStore,
    SystematicRegimeWriter,
)
from trading_agent.us_equity_calendar import regular_session_bounds

ROOT = Path(__file__).resolve().parents[1]


def test_operating_tick_runs_register_start_finalize_and_next_card(tmp_path: Path) -> None:
    # Given: a completed decision-session source and empty private ledgers.
    first_source = _source("risk_on")
    experiment = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    cards = SystematicRegimeStore(tmp_path / "systematic.sqlite3")

    # When: post-close, intraday, and the next post-close ticks run in order.
    registered = run_systematic_regime_tick(
        now=first_source.observed_at,
        code_version=CODE_VERSION,
        experiment_ledger=experiment,
        store=cards,
        source=first_source,
    )
    first_card = cards.cards()[0]
    bounds = regular_session_bounds(first_card.target_session)
    assert bounds is not None
    started = run_systematic_regime_tick(
        now=bounds[0] + dt.timedelta(minutes=1),
        code_version=CODE_VERSION,
        experiment_ledger=experiment,
        store=cards,
        source=None,
    )
    target_source = _extend_source(first_source, first_card.target_session)
    finalized = run_systematic_regime_tick(
        now=target_source.observed_at,
        code_version=CODE_VERSION,
        experiment_ledger=experiment,
        store=cards,
        source=target_source,
    )

    # Then: the first trial is terminal and the next session card is registered.
    assert registered.phase is SystematicOperatingPhase.POST_CLOSE
    assert registered.cards_created == 1
    assert started.phase is SystematicOperatingPhase.REGULAR_SESSION
    assert started.trials_started == 1
    assert finalized.trials_finalized == 1
    assert finalized.cards_created == 1
    assert len(cards.cards()) == 2
    assert len(cards.outcomes()) == 1


def test_operating_tick_does_not_publish_a_card_when_registration_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a completed source whose experiment ledger registration fails.
    source = _source("risk_on")
    experiment = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    cards = SystematicRegimeStore(tmp_path / "systematic.sqlite3")

    def reject_registration(*args: object, **kwargs: object) -> None:
        _ = args, kwargs
        raise RuntimeError("injected registration failure")

    monkeypatch.setattr(operating, "register_systematic_regime_trial", reject_registration)

    # When: the post-close operating tick cannot register its shadow trial.
    with pytest.raises(RuntimeError, match="injected registration failure"):
        _ = run_systematic_regime_tick(
            now=source.observed_at,
            code_version=CODE_VERSION,
            experiment_ledger=experiment,
            store=cards,
            source=source,
        )

    # Then: the hidden staged card is published and started by the next regular tick.
    assert cards.cards() == ()
    assert len(cards.pending_cards()) == 1
    monkeypatch.undo()
    bounds = regular_session_bounds(source.session_date + dt.timedelta(days=1))
    assert bounds is not None
    recovered = run_systematic_regime_tick(
        now=bounds[0] + dt.timedelta(minutes=1),
        code_version=CODE_VERSION,
        experiment_ledger=experiment,
        store=cards,
        source=None,
    )
    assert recovered.cards_created == 1
    assert recovered.trials_registered == 1
    assert recovered.trials_started == 1
    assert len(cards.cards()) == 1
    assert cards.pending_cards() == ()


def test_regular_tick_recovers_a_registered_but_unpublished_card(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: registration succeeds but the publication marker fails once.
    source = _source("risk_on")
    experiment = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    cards = SystematicRegimeStore(tmp_path / "systematic.sqlite3")

    def reject_publication(
        self: SystematicRegimeWriter,
        card: object,
    ) -> bool:
        _ = self, card
        raise RuntimeError("injected publication failure")

    monkeypatch.setattr(SystematicRegimeWriter, "publish_card", reject_publication)

    # When: the post-close tick stops after the durable trial registration.
    with pytest.raises(RuntimeError, match="injected publication failure"):
        _ = run_systematic_regime_tick(
            now=source.observed_at,
            code_version=CODE_VERSION,
            experiment_ledger=experiment,
            store=cards,
            source=source,
        )
    assert cards.cards() == ()
    pending = cards.pending_cards()
    assert len(pending) == 1
    assert len(experiment.multi_market_trials()) == 1
    monkeypatch.undo()

    # Then: the target-session tick publishes and starts the existing trial.
    bounds = regular_session_bounds(pending[0].target_session)
    assert bounds is not None
    recovered = run_systematic_regime_tick(
        now=bounds[0] + dt.timedelta(minutes=1),
        code_version=CODE_VERSION,
        experiment_ledger=experiment,
        store=cards,
        source=None,
    )
    assert recovered.cards_created == 1
    assert recovered.trials_registered == 0
    assert recovered.trials_started == 1
    assert cards.cards() == pending
    assert cards.pending_cards() == ()


def test_post_close_censors_a_pending_card_that_missed_its_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a registered card stayed unpublished throughout its target session.
    source = _source("risk_on")
    experiment = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    cards = SystematicRegimeStore(tmp_path / "systematic.sqlite3")

    def reject_publication(
        self: SystematicRegimeWriter,
        card: object,
    ) -> bool:
        _ = self, card
        raise RuntimeError("injected publication failure")

    monkeypatch.setattr(SystematicRegimeWriter, "publish_card", reject_publication)
    with pytest.raises(RuntimeError, match="injected publication failure"):
        _ = run_systematic_regime_tick(
            now=source.observed_at,
            code_version=CODE_VERSION,
            experiment_ledger=experiment,
            store=cards,
            source=source,
        )
    pending = cards.pending_cards()
    trial = experiment.multi_market_trials()[0].registration
    monkeypatch.undo()

    # When: operation resumes only after the missed target session closes.
    target_source = _extend_source(source, pending[0].target_session)
    recovered = run_systematic_regime_tick(
        now=target_source.observed_at,
        code_version=CODE_VERSION,
        experiment_ledger=experiment,
        store=cards,
        source=target_source,
    )

    # Then: the stale card stays hidden and its trial closes as censored evidence.
    assert recovered.trials_finalized == 1
    assert cards.pending_cards() == ()
    assert cards.expired_cards() == pending
    assert pending[0] not in cards.cards()
    assert tuple(
        item.event.event_kind
        for item in experiment.multi_market_trial_events(trial.trial_id)
    ) == (TrialEventKind.CENSORED,)


def test_regular_tick_migrates_an_existing_v1_store_before_reading(tmp_path: Path) -> None:
    # Given: the exact prior private schema exists with no staged cards.
    path = tmp_path / "systematic.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(f"{SYSTEMATIC_REGIME_SCHEMA_V1}PRAGMA user_version = 1;")
    path.chmod(0o600)
    source = _source("risk_on")
    bounds = regular_session_bounds(source.session_date)
    assert bounds is not None

    # When: the normal regular-session operating surface reads the store.
    result = run_systematic_regime_tick(
        now=bounds[0] + dt.timedelta(minutes=1),
        code_version=CODE_VERSION,
        experiment_ledger=ExperimentLedgerStore(tmp_path / "experiment.sqlite3"),
        store=SystematicRegimeStore(path),
        source=None,
    )

    # Then: preflight migrated v1 before the first query without creating work.
    assert result.cards_created == 0
    assert result.trials_started == 0
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)


def test_cli_fixture_happy_path_writes_private_recommendation_card(tmp_path: Path) -> None:
    # Given: a completed-day fixture and isolated output paths.
    source = _source("risk_on")
    fixture = _write_fixture(tmp_path, source)
    output = tmp_path / "output"
    database = tmp_path / "systematic.sqlite3"
    experiment = tmp_path / "experiment.sqlite3"

    # When: the public CLI entry point runs its post-close fixture path.
    result = cli.main(
        [
            "--session-date",
            source.session_date.isoformat(),
            "--fixture-root",
            str(fixture),
            "--database",
            str(database),
            "--experiment-ledger",
            str(experiment),
            "--output-dir",
            str(output),
        ],
        now=source.observed_at,
        runtime_code_version=CODE_VERSION,
    )

    # Then: one read-only card and aggregate report are private and explicit about authority.
    assert result == 0
    card_path = output / "us_systematic_regime_card_ko.md"
    report_path = output / "us_systematic_regime_report_ko.md"
    assert "risk_on" in card_path.read_text(encoding="utf-8")
    assert "주문 권한: 없음" in card_path.read_text(encoding="utf-8")
    assert "account access: 0" in report_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(card_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


def test_cli_historical_production_date_fails_before_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: production mode with a date different from the current New York date.
    credential_calls = 0

    def forbidden_credentials(path: Path):
        nonlocal credential_calls
        _ = path
        credential_calls += 1
        raise AssertionError("credential loader must not run")

    monkeypatch.setattr(cli, "load_private_alpaca_credentials", forbidden_credentials)

    # When: the CLI is asked to operate a historical date.
    result = cli.main(
        [
            "--session-date",
            "2026-07-20",
            "--database",
            str(tmp_path / "systematic.sqlite3"),
            "--experiment-ledger",
            str(tmp_path / "experiment.sqlite3"),
            "--output-dir",
            str(tmp_path / "output"),
        ],
        now=dt.datetime(2026, 7, 21, 17, tzinfo=dt.UTC),
        runtime_code_version=CODE_VERSION,
    )

    # Then: current-date validation blocks before credentials or provider access.
    assert result == 1
    assert credential_calls == 0


def test_cli_help_and_invalid_date_surface() -> None:
    # Given: the executable CLI.
    command = [sys.executable, str(ROOT / "run_us_systematic_regime.py")]

    # When: help and malformed input are invoked.
    help_result = subprocess.run((*command, "--help"), cwd=ROOT, capture_output=True, text=True, check=False)
    invalid = subprocess.run(
        (*command, "--session-date", "not-a-date"),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: argparse exposes help and rejects malformed input at its boundary.
    assert help_result.returncode == 0
    assert "--session-date" in help_result.stdout
    assert invalid.returncode == 2


def _write_fixture(root: Path, source: SwingDailySource) -> Path:
    fixture = root / "fixture"
    fixture.mkdir()
    manifest = {
        "schema_version": 1,
        "session_date": source.session_date.isoformat(),
        "observed_at": source.observed_at.isoformat(),
        "universe_id": source.universe_id,
        "symbols": source.symbols,
        "bars_file": "bars.json",
    }
    bars = [
        bar.model_dump(mode="json", exclude={"observed_at"})
        for bar in source.bars
    ]
    bars_payload = json.dumps(bars).encode()
    manifest["bars_sha256"] = hashlib.sha256(bars_payload).hexdigest()
    (fixture / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (fixture / "bars.json").write_bytes(bars_payload)
    return fixture

from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import run_us_systematic_regime as cli
from tests.test_systematic_regime_engine import _source
from tests.test_systematic_regime_trial import CODE_VERSION, _extend_source
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.swing_shadow_models import SwingDailySource
from trading_agent.systematic_regime_operating import (
    SystematicOperatingPhase,
    run_systematic_regime_tick,
)
from trading_agent.systematic_regime_store import SystematicRegimeStore
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

    monkeypatch.setattr(cli, "load_alpaca_credentials", forbidden_credentials)

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
    (fixture / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (fixture / "bars.json").write_text(json.dumps(bars), encoding="utf-8")
    return fixture

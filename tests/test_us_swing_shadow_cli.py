from __future__ import annotations

import datetime as dt
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import typer

import run_us_swing_shadow
from trading_agent.swing_shadow_store import ShadowEventKind, SwingShadowStore
from trading_agent.trade_signal_publication import TradeSignalPublication

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "us_swing_shadow"
SESSION = dt.date(2026, 7, 15)


def test_fixture_cli_projects_and_replays_private_swing_shadow_evidence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "ledger" / "swing-shadow.sqlite3"
    output = tmp_path / "output"

    run_us_swing_shadow.main(
        session_date=SESSION.isoformat(),
        fixture_root=str(EXAMPLE),
        database=str(database),
        output_dir=str(output),
    )
    first_report = _report(output)
    run_us_swing_shadow.main(
        session_date=SESSION.isoformat(),
        fixture_root=str(EXAMPLE),
        database=str(database),
        output_dir=str(output),
    )
    second_report = _report(output)
    terminal = capsys.readouterr().out

    store = SwingShadowStore(database)
    signals = store.signals()
    assert len(signals) == 1
    assert tuple(event.kind for event in store.events(signals[0].signal_id)) == (
        ShadowEventKind.SIGNAL_CREATED,
    )
    outbox = output / "trade-signals.v1.jsonl"
    publications = tuple(
        TradeSignalPublication.model_validate_json(line)
        for line in outbox.read_text(encoding="utf-8").splitlines()
    )
    assert len(publications) == 1
    assert publications[0].signal.signal_id == signals[0].signal_id
    assert "신규 조건부 신호: 1" in first_report
    assert "신규 shadow event: 1" in first_report
    assert "신규 조건부 신호: 0" in second_report
    assert "신규 shadow event: 0" in second_report
    combined = first_report + second_report + terminal
    for marker in (signals[0].evidence_refs[0].record_id, "fixture-universe-v1"):
        assert marker not in combined
    assert str(database) not in combined
    assert str(output) not in combined
    for path in (
        database,
        outbox,
        _report_path(output),
        *tuple((output / "trade-signal-cards-ko").iterdir()),
    ):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_fixture_cli_rejects_missing_source_before_creating_outputs(tmp_path: Path) -> None:
    database = tmp_path / "ledger" / "swing-shadow.sqlite3"
    output = tmp_path / "output"

    with pytest.raises(typer.BadParameter):
        run_us_swing_shadow.main(
            session_date=SESSION.isoformat(),
            fixture_root=str(tmp_path / "missing"),
            database=str(database),
            output_dir=str(output),
        )

    assert not database.exists()
    assert not output.exists()


def test_historical_production_request_does_not_load_credentials_or_open_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    universe = tmp_path / "universe.txt"
    universe.write_text("ACME\n", encoding="utf-8")
    calls = 0

    def unexpected_credentials(_: Path) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("credentials must not be read before current-session guard")

    monkeypatch.setattr(run_us_swing_shadow, "load_alpaca_credentials", unexpected_credentials)
    monkeypatch.setattr(
        run_us_swing_shadow,
        "_current_new_york",
        lambda: dt.datetime(2026, 7, 16, 16, 5, tzinfo=run_us_swing_shadow.NEW_YORK),
    )

    with pytest.raises(typer.BadParameter):
        run_us_swing_shadow.main(
            session_date=SESSION.isoformat(),
            universe_file=str(universe),
            database=str(tmp_path / "ledger.sqlite3"),
            output_dir=str(tmp_path / "output"),
            secret_path=str(tmp_path / "private-alpaca.env"),
        )

    assert calls == 0


def test_invalid_universe_row_does_not_load_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    universe = tmp_path / "universe.txt"
    universe.write_text("ACME,OTHER\n", encoding="utf-8")
    calls = 0

    def unexpected_credentials(_: Path) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("invalid universe must fail before credentials")

    monkeypatch.setattr(run_us_swing_shadow, "load_alpaca_credentials", unexpected_credentials)
    monkeypatch.setattr(
        run_us_swing_shadow,
        "_current_new_york",
        lambda: dt.datetime(2026, 7, 15, 16, 5, tzinfo=run_us_swing_shadow.NEW_YORK),
    )

    with pytest.raises(typer.BadParameter):
        run_us_swing_shadow.main(
            session_date=SESSION.isoformat(),
            universe_file=str(universe),
            database=str(tmp_path / "ledger.sqlite3"),
            output_dir=str(tmp_path / "output"),
            secret_path=str(tmp_path / "private-alpaca.env"),
        )

    assert calls == 0


def test_cli_rejects_output_hard_link_to_its_ledger_before_source_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "ledger.sqlite3"
    with SwingShadowStore(database).writer():
        pass
    output = tmp_path / "output"
    output.mkdir()
    os.link(database, output / "trade-signals.v1.jsonl")
    calls = 0

    def unexpected_source(*args: object, **kwargs: object) -> object:
        nonlocal calls
        _ = args, kwargs
        calls += 1
        raise AssertionError("hard-link collision must fail before fixture loading")

    monkeypatch.setattr(run_us_swing_shadow, "load_swing_daily_source", unexpected_source)

    with pytest.raises(typer.BadParameter):
        run_us_swing_shadow.main(
            session_date=SESSION.isoformat(),
            fixture_root=str(EXAMPLE),
            database=str(database),
            output_dir=str(output),
        )

    assert SwingShadowStore(database).is_initialized() is True
    assert calls == 0


def test_help_exposes_only_bounded_local_options() -> None:
    result = subprocess.run(
        [sys.executable, "run_us_swing_shadow.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    output = result.stdout + result.stderr
    for option in (
        "--session-date",
        "--universe-file",
        "--fixture-root",
        "--database",
        "--output-dir",
        "--secret-path",
    ):
        assert option in output
    assert "--arm" not in output
    assert "paper-api.alpaca.markets" not in output


def _report(output: Path) -> str:
    return _report_path(output).read_text(encoding="utf-8")


def _report_path(output: Path) -> Path:
    return output / "us_swing_shadow_summary_ko.md"

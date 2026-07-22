from __future__ import annotations

import datetime as dt
import os
import stat
import subprocess
import sys
from pathlib import Path

import httpx2
import pytest
import typer

import run_us_swing_shadow
from trading_agent.alpaca_bars import AlpacaBarsClient
from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.swing_shadow_models import SwingDailySource
from trading_agent.swing_shadow_source import load_swing_daily_source
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
    delivery = output / "hermes-delivery.sqlite3"

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
    assert "신규 Hermes 전달: 1" in first_report
    assert "신규 Hermes 전달: 0" in second_report
    assert tuple(event.kind for event in HermesDeliveryStore(delivery).events()) == (
        HermesDeliveryKind.WATCH,
    )
    combined = first_report + second_report + terminal
    for marker in (signals[0].evidence_refs[0].record_id, "fixture-universe-v1"):
        assert marker not in combined
    assert str(database) not in combined
    assert str(output) not in combined
    for path in (
        database,
        delivery,
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


def test_auto_universe_fetches_most_active_before_completed_daily_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a current post-close run and a valid read-only most-active response.
    requests: list[httpx2.Request] = []
    captured_symbols: tuple[str, ...] = ()

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "last_updated": "2026-07-15T20:05:00Z",
                "most_actives": [
                    {"symbol": "ACME", "volume": 1_000_000, "trade_count": 10_000},
                ],
            },
        )

    client = httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(respond),
    )

    def collect(
        *,
        bars_client: AlpacaBarsClient,
        symbols: tuple[str, ...],
        session_date: dt.date,
        observed_at: dt.datetime,
        universe_id: str,
        now: dt.datetime,
    ) -> SwingDailySource:
        nonlocal captured_symbols
        _ = bars_client, session_date, observed_at, universe_id, now
        captured_symbols = symbols
        return load_swing_daily_source(EXAMPLE, session_date=SESSION)

    monkeypatch.setattr(run_us_swing_shadow, "create_alpaca_client", lambda: client)
    monkeypatch.setattr(
        run_us_swing_shadow,
        "load_alpaca_credentials",
        lambda _: AlpacaCredentials("key", "secret"),
    )
    monkeypatch.setattr(run_us_swing_shadow, "collect_current_swing_daily_source", collect)
    monkeypatch.setattr(
        run_us_swing_shadow,
        "_current_new_york",
        lambda: dt.datetime(2026, 7, 15, 16, 5, tzinfo=run_us_swing_shadow.NEW_YORK),
    )

    # When: the scanner runs without a hand-written universe file.
    run_us_swing_shadow.main(
        session_date=SESSION.isoformat(),
        auto_universe=True,
        database=str(tmp_path / "ledger.sqlite3"),
        delivery_database=str(tmp_path / "delivery.sqlite3"),
        output_dir=str(tmp_path / "output"),
        secret_path=str(tmp_path / "alpaca.env"),
    )

    # Then: only the data screener is called and canonical symbols reach daily collection.
    assert captured_symbols == ("ACME",)
    assert tuple(request.url.path for request in requests) == (
        "/v1beta1/screener/stocks/most-actives",
    )


def test_historical_auto_universe_request_does_not_load_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an auto-universe request for a date other than the current NY session.
    calls = 0

    def unexpected_credentials(_: Path) -> AlpacaCredentials:
        nonlocal calls
        calls += 1
        raise AssertionError("historical auto universe must not load credentials")

    monkeypatch.setattr(run_us_swing_shadow, "load_alpaca_credentials", unexpected_credentials)
    monkeypatch.setattr(
        run_us_swing_shadow,
        "_current_new_york",
        lambda: dt.datetime(2026, 7, 16, 16, 5, tzinfo=run_us_swing_shadow.NEW_YORK),
    )

    # When/Then: causality fails before credentials, HTTP, or local output creation.
    with pytest.raises(typer.BadParameter):
        run_us_swing_shadow.main(
            session_date=SESSION.isoformat(),
            auto_universe=True,
            database=str(tmp_path / "ledger.sqlite3"),
            output_dir=str(tmp_path / "output"),
            secret_path=str(tmp_path / "alpaca.env"),
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
        "--delivery-database",
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

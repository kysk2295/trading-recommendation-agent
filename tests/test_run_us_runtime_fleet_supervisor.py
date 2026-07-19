from __future__ import annotations

import datetime as dt
import signal
from pathlib import Path

import httpx2
import pytest

import run_us_runtime_fleet_supervisor as cli
from tests.alpaca_sip_runtime_fleet_fixtures import wire_bars
from tests.test_run_us_runtime_fleet_cycle import (
    FOUNDATION,
    NOW,
    _fixture_conditional,
    _historical_response,
    _inputs,
)
from trading_agent.contract_outbox import append_trade_signal_publication
from trading_agent.data_foundation_manifest import load_data_foundation_manifest
from trading_agent.us_opportunity_scanner_projection import UsOpportunityScannerProjector
from trading_agent.us_opportunity_scanner_store import UsOpportunityScannerStore
from trading_agent.us_runtime_minute_supervisor import RuntimeSupervisorStatus
from trading_agent.us_runtime_minute_supervisor_store import RuntimeMinuteSupervisorStore


def test_help_is_available() -> None:
    with pytest.raises(SystemExit) as raised:
        _ = cli.parse_args(["--help"])
    assert raised.value.code == 0


def test_one_cycle_auto_profile_supervisor_reaches_ready(tmp_path: Path) -> None:
    scanner, _profile = _inputs(tmp_path)
    secret = tmp_path / "alpaca.env"
    secret.write_text("APCA_API_KEY_ID=fixture\nAPCA_API_SECRET_KEY=fixture\n", encoding="utf-8")
    secret.chmod(0o600)
    requests: list[httpx2.Request] = []
    assert (
        append_trade_signal_publication(
            tmp_path / "trade-signals.v1.jsonl",
            tmp_path / "cards",
            _fixture_conditional(),
        )
        is True
    )

    def client_factory() -> httpx2.Client:
        def respond(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            if request.url.params["asof"] == NOW.date().isoformat():
                return httpx2.Response(
                    200,
                    json={"bars": {"FIXT": wire_bars("FIXT", 35)}, "next_page_token": None},
                )
            return _historical_response(request)

        return httpx2.Client(
            base_url="https://data.alpaca.markets",
            transport=httpx2.MockTransport(respond),
            follow_redirects=False,
        )

    times = iter((NOW, NOW + dt.timedelta(seconds=1)))
    arguments = _arguments(tmp_path, scanner, secret)
    arguments.extend(
        [
            "--conditional-signal-outbox",
            str(tmp_path / "trade-signals.v1.jsonl"),
            "--actionability-manifest-root",
            str(tmp_path / "actionability-manifests"),
        ]
    )
    code = cli.main(
        arguments,
        clock=lambda: next(times),
        sleeper=lambda _seconds: None,
        client_factory=client_factory,
    )

    assert code == 0
    assert len(requests) == 21
    records = RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3").records()
    assert len(records) == 1
    assert records[0].status is RuntimeSupervisorStatus.READY
    assert len(tuple((tmp_path / "actionability-manifests").glob("*.json"))) == 1


def test_closed_session_stops_before_secret_or_cycle_io(tmp_path: Path) -> None:
    closed = dt.datetime(2026, 7, 19, 10, tzinfo=NOW.tzinfo)
    code = cli.main(
        _arguments(tmp_path, tmp_path / "missing.sqlite3", tmp_path / "missing.env"),
        clock=lambda: closed,
        sleeper=lambda _seconds: None,
    )

    assert code == 1
    assert not (tmp_path / "supervisor.sqlite3").exists()
    assert not (tmp_path / "audit.sqlite3").exists()


def test_partial_actionability_options_block_before_cycle_io(tmp_path: Path) -> None:
    arguments = _arguments(
        tmp_path,
        tmp_path / "missing.sqlite3",
        tmp_path / "missing.env",
    )
    arguments.extend(["--conditional-signal-outbox", str(tmp_path / "signals.jsonl")])

    assert cli.main(arguments, clock=lambda: NOW) == 1
    assert not (tmp_path / "supervisor.sqlite3").exists()
    assert not (tmp_path / "audit.sqlite3").exists()


def test_shutdown_request_stops_before_secret_or_cycle_io(tmp_path: Path) -> None:
    code = cli.main(
        _arguments(tmp_path, tmp_path / "missing.sqlite3", tmp_path / "missing.env"),
        clock=lambda: NOW,
        sleeper=lambda _seconds: (_ for _ in ()).throw(AssertionError),
        shutdown_requested=lambda: True,
    )

    assert code == 0
    assert not (tmp_path / "supervisor.sqlite3").exists()
    assert not (tmp_path / "audit.sqlite3").exists()
    report = (tmp_path / "report" / cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "result: stopped" in report
    assert "account/order mutation: 0" in report


def test_shutdown_signal_marks_controller_requested() -> None:
    previous = signal.getsignal(signal.SIGTERM)

    with cli._shutdown_signals() as shutdown:
        signal.raise_signal(signal.SIGTERM)
        assert shutdown.requested() is True
        shutdown.sleep(60.0)

    assert signal.getsignal(signal.SIGTERM) == previous


def test_two_cycle_soak_reloads_scanner_and_reuses_historical_cache(tmp_path: Path) -> None:
    scanner, _profile = _inputs(tmp_path)
    secret = tmp_path / "alpaca.env"
    secret.write_text("APCA_API_KEY_ID=fixture\nAPCA_API_SECRET_KEY=fixture\n", encoding="utf-8")
    secret.chmod(0o600)
    requests: list[httpx2.Request] = []

    def client_factory() -> httpx2.Client:
        def respond(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            if request.url.params["asof"] == NOW.date().isoformat():
                opened = dt.datetime.fromisoformat(request.url.params["start"])
                closed = dt.datetime.fromisoformat(request.url.params["end"])
                count = int((closed - opened) / dt.timedelta(minutes=1)) + 1
                return httpx2.Response(
                    200,
                    json={"bars": {"FIXT": wire_bars("FIXT", count)}, "next_page_token": None},
                )
            return _historical_response(request)

        return httpx2.Client(
            base_url="https://data.alpaca.markets",
            transport=httpx2.MockTransport(respond),
            follow_redirects=False,
        )

    second = NOW + dt.timedelta(minutes=1)
    times = iter((NOW, NOW + dt.timedelta(seconds=1), second, second + dt.timedelta(seconds=1)))

    def refresh(_seconds: float) -> None:
        store = UsOpportunityScannerStore(scanner)
        bundle = store.latest_bundle()
        assert bundle is not None
        refreshed = bundle.opportunity.model_copy(
            update={
                "opportunity_id": "us-opportunity-fix-20260717t140629z",
                "observed_at": second - dt.timedelta(seconds=1),
                "valid_until": second + dt.timedelta(minutes=1),
            },
        )
        _ = UsOpportunityScannerProjector(store, tmp_path / "scanner-canonical").project(
            refreshed,
            load_data_foundation_manifest(FOUNDATION),
        )

    arguments = _arguments(tmp_path, scanner, secret)
    arguments[arguments.index("--cycles") + 1] = "2"
    code = cli.main(
        arguments,
        clock=lambda: next(times),
        sleeper=refresh,
        client_factory=client_factory,
    )

    assert code == 0
    assert len(requests) == 22
    records = RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3").records()
    assert tuple(item.status for item in records) == (
        RuntimeSupervisorStatus.READY,
        RuntimeSupervisorStatus.READY,
    )


def test_two_cycle_soak_recovers_after_current_provider_failure(tmp_path: Path) -> None:
    scanner, _profile = _inputs(tmp_path)
    secret = tmp_path / "alpaca.env"
    secret.write_text("APCA_API_KEY_ID=fixture\nAPCA_API_SECRET_KEY=fixture\n", encoding="utf-8")
    secret.chmod(0o600)
    requests: list[httpx2.Request] = []
    current_calls = 0

    def client_factory() -> httpx2.Client:
        def respond(request: httpx2.Request) -> httpx2.Response:
            nonlocal current_calls
            requests.append(request)
            if request.url.params["asof"] != NOW.date().isoformat():
                return _historical_response(request)
            current_calls += 1
            if current_calls == 1:
                return httpx2.Response(503, content=b"provider unavailable")
            opened = dt.datetime.fromisoformat(request.url.params["start"])
            closed = dt.datetime.fromisoformat(request.url.params["end"])
            count = int((closed - opened) / dt.timedelta(minutes=1)) + 1
            return httpx2.Response(
                200,
                json={"bars": {"FIXT": wire_bars("FIXT", count)}, "next_page_token": None},
            )

        return httpx2.Client(
            base_url="https://data.alpaca.markets",
            transport=httpx2.MockTransport(respond),
            follow_redirects=False,
        )

    second = NOW + dt.timedelta(minutes=1)
    times = iter((NOW, NOW + dt.timedelta(seconds=1), second, second + dt.timedelta(seconds=1)))

    def refresh(_seconds: float) -> None:
        store = UsOpportunityScannerStore(scanner)
        bundle = store.latest_bundle()
        assert bundle is not None
        refreshed = bundle.opportunity.model_copy(
            update={
                "opportunity_id": "us-opportunity-fix-20260717t140629z",
                "observed_at": second - dt.timedelta(seconds=1),
                "valid_until": second + dt.timedelta(minutes=1),
            },
        )
        _ = UsOpportunityScannerProjector(store, tmp_path / "scanner-canonical").project(
            refreshed,
            load_data_foundation_manifest(FOUNDATION),
        )

    arguments = _arguments(tmp_path, scanner, secret)
    arguments[arguments.index("--cycles") + 1] = "2"
    code = cli.main(
        arguments,
        clock=lambda: next(times),
        sleeper=refresh,
        client_factory=client_factory,
    )

    assert code == 1
    assert len(requests) == 22
    assert current_calls == 2
    records = RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3").records()
    assert tuple(item.status for item in records) == (
        RuntimeSupervisorStatus.BLOCKED,
        RuntimeSupervisorStatus.READY,
    )


def _arguments(tmp_path: Path, scanner: Path, secret: Path) -> list[str]:
    return [
        "--scanner-store",
        str(scanner),
        "--auto-profile-root",
        str(tmp_path / "profiles"),
        "--runtime-root",
        str(tmp_path / "runtime"),
        "--canonical-root",
        str(tmp_path / "canonical"),
        "--audit-store",
        str(tmp_path / "audit.sqlite3"),
        "--policy-state-store",
        str(tmp_path / "policy.sqlite3"),
        "--supervisor-store",
        str(tmp_path / "supervisor.sqlite3"),
        "--output-dir",
        str(tmp_path / "report"),
        "--secret-path",
        str(secret),
        "--cycles",
        "1",
        "--interval-seconds",
        "60",
    ]

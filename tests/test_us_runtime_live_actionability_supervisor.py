from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx2
import pytest

import run_us_runtime_fleet_supervisor as cli
from tests.alpaca_sip_dynamic_reconnect_fixtures import ConnectorQueue, FixtureClock
from tests.alpaca_sip_runtime_fleet_fixtures import wire_bars
from tests.test_alpaca_sip_live_actionability import _EPOCH, _dependencies
from tests.test_run_us_runtime_fleet_cycle import (
    FOUNDATION,
    NOW,
    _fixture_conditional,
    _historical_response,
    _inputs,
)
from tests.test_run_us_runtime_fleet_supervisor import _arguments
from tests.test_us_runtime_live_actionability_cycle import _connection
from trading_agent.alpaca_sip_quote_actionability_store import AlpacaSipQuoteActionabilityStore
from trading_agent.contract_outbox import append_trade_signal_publication
from trading_agent.data_foundation_manifest import load_data_foundation_manifest
from trading_agent.us_opportunity_scanner_projection import UsOpportunityScannerProjector
from trading_agent.us_opportunity_scanner_store import UsOpportunityScannerStore
from trading_agent.us_runtime_minute_supervisor import RuntimeSupervisorStatus
from trading_agent.us_runtime_minute_supervisor_store import RuntimeMinuteSupervisorStore
from trading_agent.us_runtime_supervisor_live_audit import RuntimeSupervisorLiveStatus


def test_partial_live_options_block_before_supervisor_or_cycle_io(tmp_path: Path) -> None:
    arguments = _arguments(tmp_path, tmp_path / "missing.sqlite3", tmp_path / "missing.env")
    arguments.extend(_actionability_arguments(tmp_path))
    arguments.extend(["--live-actionability-receipt-root", str(tmp_path / "receipts")])

    assert cli.main(arguments, clock=lambda: NOW) == 1
    assert not (tmp_path / "supervisor.sqlite3").exists()
    assert not (tmp_path / "audit.sqlite3").exists()
    assert not (tmp_path / "policy.sqlite3").exists()


def test_armed_supervisor_forwards_fixture_websocket_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanner, _profile = _inputs(tmp_path)
    secret = tmp_path / "alpaca.env"
    secret.write_text("APCA_API_KEY_ID=fixture\nAPCA_API_SECRET_KEY=fixture\n", encoding="utf-8")
    secret.chmod(0o600)
    assert append_trade_signal_publication(
        tmp_path / "trade-signals.v1.jsonl",
        tmp_path / "cards",
        _fixture_conditional(),
    )
    queue = ConnectorQueue([_connection()])
    live_clock = FixtureClock(NOW.astimezone(dt.UTC) + dt.timedelta(seconds=1))
    dependencies = _dependencies(queue, live_clock, (_EPOCH,))
    monkeypatch.setattr(
        "trading_agent.us_runtime_live_actionability_config.default_alpaca_sip_live_actionability_dependencies",
        lambda: dependencies,
    )
    requests: list[httpx2.Request] = []

    def client_factory() -> httpx2.Client:
        def respond(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            if request.url.params["asof"] != NOW.date().isoformat():
                return _historical_response(request)
            return httpx2.Response(
                200,
                json={"bars": {"FIXT": wire_bars("FIXT", 35)}, "next_page_token": None},
            )

        return httpx2.Client(
            base_url="https://data.alpaca.markets",
            transport=httpx2.MockTransport(respond),
            follow_redirects=False,
        )

    arguments = _arguments(tmp_path, scanner, secret)
    arguments.extend(_actionability_arguments(tmp_path))
    arguments.extend(
        [
            "--arm-live-actionability",
            "--live-actionability-receipt-root",
            str(tmp_path / "live-receipts"),
            "--live-actionability-store",
            str(tmp_path / "live-actionability.sqlite3"),
        ]
    )
    times = iter((NOW, NOW + dt.timedelta(seconds=1)))

    assert (
        cli.main(
            arguments,
            clock=lambda: next(times),
            sleeper=lambda _seconds: None,
            client_factory=client_factory,
        )
        == 0
    )
    assert queue.calls == 1
    assert len(requests) == 21
    cycle_report = (tmp_path / "report" / "us_runtime_fleet_cycle_ko.md").read_text(encoding="utf-8")
    assert "live actionability: 1 selected, 1 new, 0 replay" in cycle_report
    store = RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3")
    live_records = store.live_records()
    assert len(live_records) == 1
    assert live_records[0].attempt_id == store.records()[0].attempt_id
    assert live_records[0].status is RuntimeSupervisorLiveStatus.COMPLETED
    assert (
        live_records[0].selected_count,
        live_records[0].created_count,
        live_records[0].replay_count,
    ) == (1, 1, 0)


def test_two_minute_armed_supervisor_reuses_same_signal_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanner, _profile = _inputs(tmp_path)
    secret = tmp_path / "alpaca.env"
    secret.write_text("APCA_API_KEY_ID=fixture\nAPCA_API_SECRET_KEY=fixture\n", encoding="utf-8")
    secret.chmod(0o600)
    publication = _fixture_conditional()
    payload = publication.model_dump(mode="json")
    payload["signal"]["valid_until"] = (NOW + dt.timedelta(minutes=2)).isoformat()
    assert append_trade_signal_publication(
        tmp_path / "trade-signals.v1.jsonl",
        tmp_path / "cards",
        type(publication).model_validate(payload),
    )
    queue = ConnectorQueue([_connection()])
    dependencies = _dependencies(
        queue,
        FixtureClock(NOW.astimezone(dt.UTC) + dt.timedelta(seconds=1)),
        (_EPOCH,),
    )
    monkeypatch.setattr(
        "trading_agent.us_runtime_live_actionability_config.default_alpaca_sip_live_actionability_dependencies",
        lambda: dependencies,
    )
    requests: list[httpx2.Request] = []

    def client_factory() -> httpx2.Client:
        def respond(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            if request.url.params["asof"] != NOW.date().isoformat():
                return _historical_response(request)
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
    arguments.extend(_actionability_arguments(tmp_path))
    arguments.extend(
        [
            "--dynamic-plan-store",
            str(tmp_path / "dynamic-plans.sqlite3"),
            "--arm-live-actionability",
            "--live-actionability-receipt-root",
            str(tmp_path / "live-receipts"),
            "--live-actionability-store",
            str(tmp_path / "live-actionability.sqlite3"),
        ]
    )

    code = cli.main(
        arguments,
        clock=lambda: next(times),
        sleeper=refresh,
        client_factory=client_factory,
    )

    assert code == 0
    assert queue.calls == 1
    assert len(requests) == 22
    assert len(tuple((tmp_path / "actionability-manifests").glob("*.json"))) == 2
    assert len(tuple((tmp_path / "live-receipts").glob("*.sqlite3"))) == 1
    assert len(AlpacaSipQuoteActionabilityStore(tmp_path / "live-actionability.sqlite3").records()) == 1
    supervisor = RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3")
    assert tuple(record.status for record in supervisor.records()) == (
        RuntimeSupervisorStatus.READY,
        RuntimeSupervisorStatus.READY,
    )
    assert tuple(
        (record.selected_count, record.created_count, record.replay_count) for record in supervisor.live_records()
    ) == ((1, 1, 0), (1, 0, 1))


def _actionability_arguments(tmp_path: Path) -> list[str]:
    return [
        "--conditional-signal-outbox",
        str(tmp_path / "trade-signals.v1.jsonl"),
        "--actionability-manifest-root",
        str(tmp_path / "actionability-manifests"),
    ]

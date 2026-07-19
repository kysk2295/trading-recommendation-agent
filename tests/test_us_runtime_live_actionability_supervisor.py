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
    NOW,
    _fixture_conditional,
    _historical_response,
    _inputs,
)
from tests.test_run_us_runtime_fleet_supervisor import _arguments
from tests.test_us_runtime_live_actionability_cycle import _connection
from trading_agent.contract_outbox import append_trade_signal_publication
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


def _actionability_arguments(tmp_path: Path) -> list[str]:
    return [
        "--conditional-signal-outbox",
        str(tmp_path / "trade-signals.v1.jsonl"),
        "--actionability-manifest-root",
        str(tmp_path / "actionability-manifests"),
    ]

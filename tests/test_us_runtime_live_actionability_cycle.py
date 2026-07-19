from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx2
import pytest

import run_us_runtime_fleet_cycle as cli
from tests import test_alpaca_sip_dynamic_feature_bridge as trade_fixtures
from tests import test_alpaca_sip_dynamic_projection as dynamic_fixtures
from tests import test_alpaca_sip_dynamic_quote_feature_bridge as quote_fixtures
from tests.alpaca_sip_dynamic_reconnect_fixtures import (
    ConnectorQueue,
    FakeConnection,
    FixtureClock,
)
from tests.alpaca_sip_runtime_fleet_fixtures import wire_bars
from tests.test_alpaca_sip_live_actionability import _EPOCH, _dependencies
from tests.test_run_us_runtime_fleet_cycle import (
    NOW,
    _arguments,
    _fixture_conditional,
    _inputs,
)
from trading_agent.contract_outbox import append_trade_signal_publication
from trading_agent.us_runtime_supervisor_live_audit import RuntimeSupervisorLiveStatus


def test_partial_live_options_block_before_policy_or_provider(tmp_path: Path) -> None:
    arguments = _arguments(
        tmp_path,
        tmp_path / "missing-profile.json",
        tmp_path / "report",
        actionability_manifests=True,
    )
    arguments.extend(["--live-actionability-receipt-root", str(tmp_path / "receipts")])

    result = cli.run_cycle(arguments, now=NOW)
    assert result.exit_code == 1
    assert result.live_outcome.status is RuntimeSupervisorLiveStatus.NOT_ATTEMPTED
    assert not (tmp_path / "policy-state.sqlite3").exists()
    assert not (tmp_path / "receipts").exists()


def test_armed_cycle_captures_and_live_stage_replays_on_cycle_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanner, profile = _inputs(tmp_path)
    secret = tmp_path / "alpaca.env"
    secret.write_text("APCA_API_KEY_ID=fixture\nAPCA_API_SECRET_KEY=fixture\n", encoding="utf-8")
    secret.chmod(0o600)
    assert append_trade_signal_publication(
        tmp_path / "trade-signals.v1.jsonl",
        tmp_path / "cards",
        _fixture_conditional(),
    )
    queue = ConnectorQueue([_connection()])
    clock = FixtureClock(NOW.astimezone(dt.UTC) + dt.timedelta(seconds=1))
    dependencies = _dependencies(queue, clock, (_EPOCH,))
    monkeypatch.setattr(
        "trading_agent.us_runtime_live_actionability_config.default_alpaca_sip_live_actionability_dependencies",
        lambda: dependencies,
    )

    def client_factory() -> httpx2.Client:
        def respond(_request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                json={"bars": {"FIXT": wire_bars("FIXT", 35)}, "next_page_token": None},
            )

        return httpx2.Client(
            base_url="https://data.alpaca.markets",
            transport=httpx2.MockTransport(respond),
            follow_redirects=False,
        )

    arguments = _arguments(
        tmp_path,
        profile,
        tmp_path / "report",
        scanner=scanner,
        secret=secret,
        actionability_manifests=True,
    )
    arguments.extend(
        [
            "--arm-live-actionability",
            "--live-actionability-receipt-root",
            str(tmp_path / "live-receipts"),
            "--live-actionability-store",
            str(tmp_path / "live-actionability.sqlite3"),
        ]
    )

    first = cli.run_cycle(arguments, now=NOW, client_factory=client_factory)
    assert first.exit_code == 0
    assert first.live_outcome.status is RuntimeSupervisorLiveStatus.COMPLETED
    assert (first.live_outcome.selected_count, first.live_outcome.created_count) == (1, 1)
    assert queue.calls == 1
    report = (tmp_path / "report" / cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "live actionability: 1 selected, 1 new, 0 replay" in report

    replay_queue = ConnectorQueue([_connection()])
    replay_dependencies = _dependencies(
        replay_queue,
        FixtureClock(NOW.astimezone(dt.UTC) + dt.timedelta(seconds=2)),
        ("2" * 32,),
    )
    monkeypatch.setattr(
        "trading_agent.us_runtime_live_actionability_config.default_alpaca_sip_live_actionability_dependencies",
        lambda: replay_dependencies,
    )

    replay = cli.run_cycle(arguments, now=NOW, client_factory=client_factory)
    assert replay.exit_code == 1
    assert replay.live_outcome.status is RuntimeSupervisorLiveStatus.COMPLETED
    assert (replay.live_outcome.selected_count, replay.live_outcome.replay_count) == (1, 1)
    assert replay_queue.calls == 0
    replay_report = (tmp_path / "report" / cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "fleet: degraded" in replay_report
    assert "live actionability: 1 selected, 0 new, 1 replay" in replay_report


def _connection() -> FakeConnection:
    timestamp = NOW.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    quote = quote_fixtures._quote(
        100.01,
        100.03,
        symbol="FIXT",
        bid_size=300,
        ask_size=100,
    )
    quote["t"] = timestamp
    trade = trade_fixtures._trade(101, 100.02, symbol="FIXT")
    trade["t"] = timestamp
    frame = dynamic_fixtures._frame(quote, trade)
    return FakeConnection(
        [
            dynamic_fixtures._connected(),
            dynamic_fixtures._authenticated(),
            (
                b'[{"T":"subscription","trades":["FIXT"],"quotes":["FIXT"],'
                b'"bars":[],"updatedBars":[],"dailyBars":[],"statuses":[],"lulds":[],'
                b'"corrections":["FIXT"],"cancelErrors":["FIXT"]}]'
            ),
            *(frame for _ in range(10)),
        ]
    )

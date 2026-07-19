from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import httpx2

import run_us_runtime_fleet_supervisor as cli
from tests.alpaca_sip_runtime_fleet_fixtures import wire_bars
from tests.test_run_us_runtime_fleet_cycle import (
    FOUNDATION,
    NOW,
    _fixture_conditional,
    _historical_response,
    _inputs,
)
from tests.test_run_us_runtime_fleet_supervisor import _arguments
from trading_agent.contract_outbox import append_trade_signal_publication
from trading_agent.data_foundation_manifest import load_data_foundation_manifest
from trading_agent.us_opportunity_scanner_projection import UsOpportunityScannerProjector
from trading_agent.us_opportunity_scanner_store import UsOpportunityScannerStore


def test_two_runtime_minutes_share_one_dynamic_plan_epoch(tmp_path: Path) -> None:
    scanner, _profile = _inputs(tmp_path)
    secret = tmp_path / "alpaca.env"
    secret.write_text(
        "APCA_API_KEY_ID=fixture\nAPCA_API_SECRET_KEY=fixture\n",
        encoding="utf-8",
    )
    secret.chmod(0o600)
    publication = _fixture_conditional()
    payload = publication.model_dump(mode="json")
    payload["signal"]["valid_until"] = (NOW + dt.timedelta(minutes=2)).isoformat()
    assert append_trade_signal_publication(
        tmp_path / "trade-signals.v1.jsonl",
        tmp_path / "cards",
        type(publication).model_validate(payload),
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
    arguments.extend(
        [
            "--conditional-signal-outbox",
            str(tmp_path / "trade-signals.v1.jsonl"),
            "--actionability-manifest-root",
            str(tmp_path / "actionability-manifests"),
            "--dynamic-plan-store",
            str(tmp_path / "dynamic-plans.sqlite3"),
        ]
    )

    code = cli.main(
        arguments,
        clock=lambda: next(times),
        sleeper=refresh,
        client_factory=client_factory,
    )

    assert code == 0
    with sqlite3.connect(tmp_path / "dynamic-plans.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM dynamic_plan").fetchone() == (1,)
    assert len(tuple((tmp_path / "actionability-manifests").glob("*.json"))) == 2
    assert len(requests) == 22

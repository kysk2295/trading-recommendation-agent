from __future__ import annotations

import datetime as dt
import os
import sqlite3
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest

import run_alpaca_paper_entry_smoke as smoke_cli
import trading_agent.research_agent_operations_sqlite as source_sqlite
from tests.paper_runtime_fixtures import market_clock
from tests.paper_trade_update_ingestion_fixtures import recovery_state
from tests.test_paper_entry_source import EVALUATED_AT, _write_valid_source
from tests.test_paper_smoke_e2e import _PhaseStream
from tests.trade_update_ledger_fixtures import FINGERPRINT
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_entry_source import (
    InvalidCurrentOrbPaperEntrySourceError,
    load_current_orb_paper_entry,
)
from trading_agent.paper_execution_models import PaperBrokerState, PaperMarketClockSnapshot
from trading_agent.paper_operating_session_models import PaperOperatingSession
from trading_agent.paper_stream_owner import PaperStreamOwnerDependencies
from trading_agent.paper_trade_update_runtime import (
    PaperOperatingSessionDependencies,
    _open_paper_operating_session,
)


def test_current_orb_source_reaches_paper_transport_only_after_reconciled_risk_approval(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    watch_database = tmp_path / "watch.sqlite3"
    output = tmp_path / "report"
    _write_valid_source(watch_database)
    store = ExecutionStore(database)
    with store.writer() as writer:
        _ = writer.bind_account(FINGERPRINT, EVALUATED_AT.astimezone(dt.UTC))
    stream = _PhaseStream(EVALUATED_AT.astimezone(dt.UTC))
    requests: list[httpx2.Request] = []

    def clock() -> dt.datetime:
        return EVALUATED_AT.astimezone(dt.UTC) + dt.timedelta(seconds=stream.heartbeat_count - 2)

    def state_for(ledger: ReconciliationLedger):
        state = recovery_state(
            ledger.unresolved_intent_ids,
            EVALUATED_AT.astimezone(dt.UTC) + dt.timedelta(seconds=stream.heartbeat_count - 1.5),
        )
        if not state.targeted_orders:
            return state
        stored = store.intents()[0]
        targeted = tuple(
            replace(
                order,
                client_order_id=stored.intent_id,
                symbol=stored.symbol,
                side=stored.side,
                quantity=Decimal(stored.quantity),
                limit_price=stored.entry_limit,
            )
            for order in state.targeted_orders
        )
        return replace(state, targeted_orders=targeted)

    def recovery_state_loader(
        _: AlpacaPaperCredentials,
        ledger: ReconciliationLedger,
    ):
        return state_for(ledger)

    def runtime_state_loader(
        _: AlpacaPaperCredentials,
    ) -> tuple[PaperBrokerState, PaperMarketClockSnapshot]:
        observed_at = EVALUATED_AT.astimezone(dt.UTC) + dt.timedelta(seconds=stream.heartbeat_count - 1.5)
        state = state_for(store.reconciliation_ledger())
        return (
            state.broker_state,
            replace(
                market_clock(),
                observed_at=observed_at,
                market_timestamp=observed_at.astimezone(dt.timezone(dt.timedelta(hours=-4))),
            ),
        )

    @contextmanager
    def stream_opener(_: AlpacaPaperCredentials) -> Iterator[_PhaseStream]:
        yield stream

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            request=request,
            headers={"X-Request-ID": "request-entry-1"},
            json={
                "id": "entry-1",
                "client_order_id": "2026-07-14T09:36:02-04:00:AAPL:opening_range_breakout",
                "symbol": "AAPL",
                "side": "buy",
                "status": "accepted",
                "qty": "1",
                "filled_qty": "0",
                "filled_avg_price": None,
                "limit_price": "10.0",
                "stop_price": None,
                "type": "limit",
                "order_class": "simple",
                "time_in_force": "day",
                "extended_hours": False,
            },
        )

    @contextmanager
    def broker_opener(credentials: AlpacaPaperCredentials):
        with httpx2.Client(
            base_url="https://paper-api.alpaca.markets",
            transport=httpx2.MockTransport(handle),
            follow_redirects=False,
        ) as client:
            from trading_agent.alpaca_paper_mutation_client import AlpacaPaperMutationClient

            yield AlpacaPaperMutationClient(client, credentials, _clock=clock)

    dependencies = PaperOperatingSessionDependencies(
        PaperStreamOwnerDependencies(recovery_state_loader, stream_opener, clock),
        runtime_state_loader,
        clock,
        broker_opener,
    )

    @contextmanager
    def session_opener(
        credentials: AlpacaPaperCredentials,
        execution_store: ExecutionStore,
    ) -> Iterator[PaperOperatingSession]:
        with _open_paper_operating_session(credentials, execution_store, dependencies) as session:
            yield session

    code = smoke_cli.main(
        (
            "--arm-paper-mutation",
            "ARM_ALPACA_PAPER_ONLY",
            "--database",
            str(database),
            "--output-dir",
            str(output),
            "--watch-database",
            str(watch_database),
        ),
        credential_loader=lambda: AlpacaPaperCredentials("test-key", "test-secret"),
        session_opener=session_opener,
        clock=lambda: EVALUATED_AT,
    )

    assert code == 0
    assert [(request.method, str(request.url)) for request in requests] == [
        ("POST", "https://paper-api.alpaca.markets/v2/orders"),
    ]
    assert tuple(event.event.event_type.value for event in store.paper_mutation_events()) == (
        "attempted",
        "acknowledged",
    )
    assert store.intents()[0].symbol == "AAPL"
    assert stream.heartbeat_count >= 4


def test_untrusted_watch_database_stops_smoke_before_credentials_or_session(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    source = tmp_path / "source.sqlite3"
    watch_database = tmp_path / "watch.sqlite3"
    output = tmp_path / "report"
    _write_valid_source(source)
    watch_database.symlink_to(source)
    store = ExecutionStore(database)
    with store.writer() as writer:
        _ = writer.bind_account(FINGERPRINT, EVALUATED_AT.astimezone(dt.UTC))
    credential_calls: list[None] = []
    session_calls: list[None] = []

    def credential_loader() -> AlpacaPaperCredentials:
        credential_calls.append(None)
        return AlpacaPaperCredentials("test-key", "test-secret")

    def session_opener(
        _: AlpacaPaperCredentials,
        __: ExecutionStore,
    ) -> AbstractContextManager[PaperOperatingSession]:
        session_calls.append(None)
        raise AssertionError("untrusted source must block before session open")

    code = smoke_cli.main(
        (
            "--arm-paper-mutation",
            "ARM_ALPACA_PAPER_ONLY",
            "--database",
            str(database),
            "--output-dir",
            str(output),
            "--watch-database",
            str(watch_database),
        ),
        credential_loader=credential_loader,
        session_opener=session_opener,
        clock=lambda: EVALUATED_AT,
    )

    assert code == 2
    assert credential_calls == []
    assert session_calls == []
    assert "InvalidCurrentOrbPaperEntrySourceError" in (output / "paper_entry_smoke_ko.md").read_text(encoding="utf-8")


def test_current_orb_loader_rejects_database_path_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "watch.sqlite3"
    replacement = tmp_path / "replacement.sqlite3"
    _write_valid_source(database)
    _write_valid_source(replacement)
    original_connect = source_sqlite.sqlite3.connect

    def replace_path_during_connection(database_uri: str, *, uri: bool) -> sqlite3.Connection:
        connection = original_connect(database_uri, uri=uri)
        os.replace(replacement, database)
        return connection

    monkeypatch.setattr(source_sqlite.sqlite3, "connect", replace_path_during_connection)

    with pytest.raises(InvalidCurrentOrbPaperEntrySourceError):
        _ = load_current_orb_paper_entry(database, EVALUATED_AT)

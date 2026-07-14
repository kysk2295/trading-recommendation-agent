from __future__ import annotations

import datetime as dt
import inspect
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

import trading_agent.paper_trade_update_runtime as trade_update_runtime
from tests.paper_runtime_fixtures import candidate, latest_bar, market_clock
from tests.paper_trade_update_ingestion_fixtures import (
    TradeUpdateStream,
    broker_state,
    state_loader,
)
from tests.trade_update_ledger_fixtures import OBSERVED_AT
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.execution_store import ExecutionStore, WriterLeaseUnavailableError
from trading_agent.paper_execution_models import PaperBrokerState, PaperMarketClockSnapshot
from trading_agent.paper_operating_session import LEDGER_GENERATION_CHANGED
from trading_agent.paper_operating_session_models import (
    InactivePaperOperatingSessionError,
    PaperOrderAdmissionRequest,
)
from trading_agent.paper_order_gate_models import (
    ApprovedPaperOrderGateDecision,
    BlockedPaperOrderGateDecision,
)
from trading_agent.paper_stream_owner import PaperStreamOwnerDependencies
from trading_agent.paper_trade_update_runtime import (
    PaperOperatingSessionDependencies,
    _open_paper_operating_session,
)


def test_public_operating_session_owns_credentials_and_execution_store_only() -> None:
    # Given: the production trade-update runtime module.
    operating_session = getattr(
        trade_update_runtime,
        "open_paper_operating_session",
        None,
    )

    # When: its supported public construction boundary is inspected.
    parameters = () if operating_session is None else tuple(inspect.signature(operating_session).parameters)

    # Then: callers cannot inject a stream, REST provider, clock, writer, or approval proof.
    assert parameters == ("credentials", "store")


def test_operating_session_surface_serializes_ingestion_and_admission() -> None:
    # Given: the supported operating-session protocol.
    protocol = getattr(trade_update_runtime, "PaperOperatingSession", None)

    # When: its public operations are inspected.
    operations = frozenset() if protocol is None else frozenset(protocol.__dict__)

    # Then: one owner exposes both stream ingestion and candidate admission.
    assert {"ingest_next", "evaluate_order"} <= operations


def test_operating_session_has_a_private_provider_injection_seam_for_contract_tests() -> None:
    # Given: the production module with a sealed public constructor.
    private_opener = getattr(
        trade_update_runtime,
        "_open_paper_operating_session",
        None,
    )

    # When: contract-test construction is inspected.
    parameters = () if private_opener is None else tuple(inspect.signature(private_opener).parameters)

    # Then: all injected adapters are grouped behind one typed dependency object.
    assert parameters == ("credentials", "store", "dependencies")


def test_operating_session_recovers_and_evaluates_with_one_stream_and_writer(
    tmp_path: Path,
) -> None:
    # Given: one execution store and one authenticated stream shared by recovery and admission.
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    stream = TradeUpdateStream()
    stream_open_count = 0

    @contextmanager
    def stream_opener(
        _: AlpacaPaperCredentials,
    ) -> Iterator[TradeUpdateStream]:
        nonlocal stream_open_count
        stream_open_count += 1
        yield stream

    evaluated_at = OBSERVED_AT + dt.timedelta(seconds=4)

    def runtime_state_loader(
        _: AlpacaPaperCredentials,
    ) -> tuple[PaperBrokerState, PaperMarketClockSnapshot]:
        observed_at = OBSERVED_AT + dt.timedelta(seconds=3, milliseconds=500)
        clock = replace(
            market_clock(),
            observed_at=observed_at,
            market_timestamp=evaluated_at.astimezone(dt.timezone(dt.timedelta(hours=-4))),
        )
        return broker_state(observed_at), clock

    dependencies = PaperOperatingSessionDependencies(
        PaperStreamOwnerDependencies(
            state_loader(stream),
            stream_opener,
            lambda: evaluated_at,
        ),
        runtime_state_loader,
        lambda: evaluated_at,
    )

    # When: candidate admission runs inside the integrated operating session.
    with _open_paper_operating_session(
        AlpacaPaperCredentials("test-key", "test-secret"),
        store,
        dependencies,
    ) as session:
        with pytest.raises(WriterLeaseUnavailableError), store.writer():
            pytest.fail("운영 세션 중 두 번째 Writer가 열리면 안 됩니다")
        request = PaperOrderAdmissionRequest(latest_bar(), candidate(), 100, 20.0)
        decision = session.evaluate_order(request)

    # Then: one WSS performs startup/current-epoch recovery and the closed owner cannot approve again.
    assert isinstance(decision, ApprovedPaperOrderGateDecision)
    assert stream_open_count == 1
    assert stream.heartbeat_count == 6
    assert len(store.paper_stream_recoveries()) == 2
    with pytest.raises(InactivePaperOperatingSessionError):
        _ = session.evaluate_order(request)


def test_operating_session_blocks_when_ledger_changes_after_current_epoch_recovery(
    tmp_path: Path,
) -> None:
    # Given: an out-of-band SQLite writer that commits after current-epoch recovery.
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    stream = TradeUpdateStream()

    @contextmanager
    def stream_opener(
        _: AlpacaPaperCredentials,
    ) -> Iterator[TradeUpdateStream]:
        yield stream

    evaluated_at = OBSERVED_AT + dt.timedelta(seconds=4)

    def runtime_state_loader(
        _: AlpacaPaperCredentials,
    ) -> tuple[PaperBrokerState, PaperMarketClockSnapshot]:
        with sqlite3.connect(store.path) as connection:
            _ = connection.execute(
                "INSERT INTO order_intents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "out-of-band",
                    "orb",
                    "1",
                    "OUT",
                    evaluated_at.isoformat(),
                    "buy",
                    "10",
                    "9",
                    "11",
                    "12",
                    1,
                ),
            )
        observed_at = OBSERVED_AT + dt.timedelta(seconds=3, milliseconds=500)
        return broker_state(observed_at), replace(
            market_clock(),
            observed_at=observed_at,
            market_timestamp=evaluated_at.astimezone(dt.timezone(dt.timedelta(hours=-4))),
        )

    dependencies = PaperOperatingSessionDependencies(
        PaperStreamOwnerDependencies(state_loader(stream), stream_opener, lambda: evaluated_at),
        runtime_state_loader,
        lambda: evaluated_at,
    )

    # When: admission crosses the generation barrier.
    with _open_paper_operating_session(
        AlpacaPaperCredentials("test-key", "test-secret"),
        store,
        dependencies,
    ) as session:
        decision = session.evaluate_order(PaperOrderAdmissionRequest(latest_bar(), candidate(), 100, 20.0))

    # Then: the stale approval is replaced by an explicit reconciliation block.
    assert isinstance(decision, BlockedPaperOrderGateDecision)
    assert decision.reasons == (LEDGER_GENERATION_CHANGED,)

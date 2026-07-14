from __future__ import annotations

import datetime as dt
import inspect
from dataclasses import replace

import pytest

import trading_agent.paper_order_gate as paper_order_gate_module
import trading_agent.paper_runtime_session as paper_runtime_session_module
from tests.paper_runtime_fixtures import (
    FakeLedgerReader,
    FakeReadyStream,
    account,
    candidate,
    credentials,
    latest_bar,
    ledger,
    market_clock,
    partial_state,
    stream_opener,
)
from trading_agent.alpaca_paper_order_stream import PaperStreamEpoch
from trading_agent.paper_execution_models import PaperBrokerState
from trading_agent.paper_order_gate_models import (
    ApprovedPaperOrderGateDecision,
    PaperOrderGateState,
)
from trading_agent.paper_risk import PaperRiskConfig
from trading_agent.paper_runtime import PaperRuntimeEpochChangedError
from trading_agent.paper_runtime_session import (
    InactivePaperRuntimeSessionError,
    _open_paper_runtime_session,
    _probe_paper_runtime,
    open_paper_runtime_session,
    probe_paper_runtime,
)


def test_supported_runtime_approval_surface_has_no_forgeable_proof_or_gate() -> None:
    assert not hasattr(
        paper_runtime_session_module,
        "PaperRuntimeReconciliationProof",
    )
    assert not hasattr(paper_runtime_session_module, "PaperRuntimeSession")
    assert not hasattr(paper_order_gate_module, "evaluate_paper_order_gate")
    assert tuple(inspect.signature(open_paper_runtime_session).parameters) == (
        "credentials",
        "ledger_reader",
    )
    assert tuple(inspect.signature(probe_paper_runtime).parameters) == (
        "credentials",
        "ledger_reader",
    )


def test_probe_reconciles_rest_and_ledger_between_two_live_heartbeats() -> None:
    stream = FakeReadyStream()
    ledger_reader = FakeLedgerReader(stream, ledger())

    readiness = _probe_paper_runtime(
        credentials(),
        ledger_reader,
        state_loader=lambda _: (PaperBrokerState(account(), (), ()), market_clock()),
        stream_opener=stream_opener(stream),
    )

    assert readiness.ready is True
    assert readiness.reconciliation.ready is True
    assert readiness.stream_heartbeat.pong_at.second == 2
    assert stream.heartbeat_count == 2
    assert ledger_reader.read_count == 1
    assert stream.active is False


def test_live_session_aggregates_partial_fill_and_sizes_candidate_internally() -> None:
    stream = FakeReadyStream()
    ledger_reader = FakeLedgerReader(stream, ledger(with_existing=True))
    evaluated_at = dt.datetime(2026, 7, 14, 13, 36, 3, tzinfo=dt.UTC)

    with _open_paper_runtime_session(
        credentials(),
        ledger_reader,
        state_loader=lambda _: (partial_state(), market_clock()),
        stream_opener=stream_opener(stream),
        _clock=lambda: evaluated_at,
    ) as session:
        decision = session.evaluate_order(
            latest_bar=latest_bar(),
            candidate_intent=candidate(),
            liquidity_allowed_quantity=1_000,
            estimated_spread_bps=0.0,
        )
        assert stream.active is True

    assert isinstance(decision, ApprovedPaperOrderGateDecision)
    assert decision.sized_order.quantity == 53
    assert decision.sized_order.planned_risk <= 75
    assert stream.active is False


def test_live_session_applies_one_cost_config_to_existing_and_candidate_risk() -> None:
    stream = FakeReadyStream()
    ledger_reader = FakeLedgerReader(stream, ledger(with_existing=True))

    with _open_paper_runtime_session(
        credentials(),
        ledger_reader,
        state_loader=lambda _: (partial_state(), market_clock()),
        stream_opener=stream_opener(stream),
        _clock=lambda: dt.datetime(2026, 7, 14, 13, 36, 3, tzinfo=dt.UTC),
    ) as session:
        decision = session.evaluate_order(
            latest_bar=latest_bar(),
            candidate_intent=candidate(),
            liquidity_allowed_quantity=1_000,
            estimated_spread_bps=0.0,
            config=PaperRiskConfig(per_side_cost_bps=50.0),
        )

    assert decision.state is PaperOrderGateState.PORTFOLIO_BLOCKED


def test_closed_runtime_session_cannot_replay_an_approval() -> None:
    stream = FakeReadyStream()
    ledger_reader = FakeLedgerReader(stream, ledger())
    with _open_paper_runtime_session(
        credentials(),
        ledger_reader,
        state_loader=lambda _: (PaperBrokerState(account(), (), ()), market_clock()),
        stream_opener=stream_opener(stream),
        _clock=lambda: dt.datetime(2026, 7, 14, 13, 36, 3, tzinfo=dt.UTC),
    ) as session:
        pass

    with pytest.raises(InactivePaperRuntimeSessionError):
        _ = session.evaluate_order(
            latest_bar=latest_bar(),
            candidate_intent=candidate(),
            liquidity_allowed_quantity=1_000,
            estimated_spread_bps=0.0,
        )


def test_live_session_fails_closed_on_torn_partial_fill_state() -> None:
    stream = FakeReadyStream()
    ledger_reader = FakeLedgerReader(stream, ledger(with_existing=True))

    with _open_paper_runtime_session(
        credentials(),
        ledger_reader,
        state_loader=lambda _: (
            partial_state(include_position=False),
            market_clock(),
        ),
        stream_opener=stream_opener(stream),
        _clock=lambda: dt.datetime(2026, 7, 14, 13, 36, 3, tzinfo=dt.UTC),
    ) as session:
        decision = session.evaluate_order(
            latest_bar=latest_bar(),
            candidate_intent=candidate(),
            liquidity_allowed_quantity=1_000,
            estimated_spread_bps=0.0,
        )

    assert decision.state is PaperOrderGateState.PORTFOLIO_BLOCKED


def test_runtime_reconciliation_admission_precedes_session_decision() -> None:
    stream = FakeReadyStream()
    ledger_reader = FakeLedgerReader(stream, ledger(with_existing=True))
    closed_clock = replace(market_clock(), is_open=False)

    with _open_paper_runtime_session(
        credentials(),
        ledger_reader,
        state_loader=lambda _: (
            PaperBrokerState(account(), (), ()),
            closed_clock,
        ),
        stream_opener=stream_opener(stream),
        _clock=lambda: dt.datetime(2026, 7, 14, 13, 36, 3, tzinfo=dt.UTC),
    ) as session:
        decision = session.evaluate_order(
            latest_bar=latest_bar(),
            candidate_intent=candidate(),
            liquidity_allowed_quantity=1_000,
            estimated_spread_bps=0.0,
        )

    assert decision.state is PaperOrderGateState.RECONCILIATION_BLOCKED


def test_probe_reports_stale_rest_receipt_as_not_ready() -> None:
    stream = FakeReadyStream()
    ledger_reader = FakeLedgerReader(stream, ledger())
    stale_account = replace(
        account(),
        observed_at=dt.datetime(2026, 7, 14, 13, 35, 50, tzinfo=dt.UTC),
    )

    readiness = _probe_paper_runtime(
        credentials(),
        ledger_reader,
        state_loader=lambda _: (
            PaperBrokerState(stale_account, (), ()),
            market_clock(),
        ),
        stream_opener=stream_opener(stream),
    )

    assert readiness.ready is False
    assert any("수신 시각" in reason for reason in readiness.reasons)


def test_live_session_rejects_state_older_than_the_freshness_boundary() -> None:
    stream = FakeReadyStream()
    ledger_reader = FakeLedgerReader(stream, ledger())
    stale_account = replace(
        account(),
        observed_at=dt.datetime(2026, 7, 14, 13, 35, 50, tzinfo=dt.UTC),
    )

    with _open_paper_runtime_session(
        credentials(),
        ledger_reader,
        state_loader=lambda _: (
            PaperBrokerState(stale_account, (), ()),
            market_clock(),
        ),
        stream_opener=stream_opener(stream),
        _clock=lambda: dt.datetime(2026, 7, 14, 13, 36, 3, tzinfo=dt.UTC),
    ) as session:
        decision = session.evaluate_order(
            latest_bar=latest_bar(),
            candidate_intent=candidate(),
            liquidity_allowed_quantity=1_000,
            estimated_spread_bps=0.0,
        )

    assert decision.state is PaperOrderGateState.RECONCILIATION_BLOCKED


def test_runtime_rejects_rest_state_spanning_two_connection_epochs() -> None:
    stream = FakeReadyStream(
        (PaperStreamEpoch("epoch-1"), PaperStreamEpoch("epoch-2"))
    )

    with pytest.raises(PaperRuntimeEpochChangedError, match="연결 세대"):
        _ = _probe_paper_runtime(
            credentials(),
            FakeLedgerReader(stream, ledger()),
            state_loader=lambda _: (
                PaperBrokerState(account(), (), ()),
                market_clock(),
            ),
            stream_opener=stream_opener(stream),
        )

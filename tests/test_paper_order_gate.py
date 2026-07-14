from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from tests.paper_order_gate_fixtures import (
    NEW_YORK,
)
from tests.paper_order_gate_fixtures import (
    at as _at,
)
from tests.paper_order_gate_fixtures import (
    candidate as _candidate,
)
from tests.paper_order_gate_fixtures import (
    evaluate as _evaluate,
)
from tests.paper_order_gate_fixtures import (
    snapshot as _snapshot,
)
from trading_agent.paper_execution_models import (
    PaperMarketClockSnapshot,
)
from trading_agent.paper_order_gate_models import (
    ApprovedPaperOrderGateDecision,
    LatestCompletedBar,
    PaperOrderGateState,
)


def test_gate_approves_only_a_fully_current_reconciled_portfolio() -> None:
    decision = _evaluate(_snapshot())

    assert decision.state is PaperOrderGateState.APPROVED
    assert decision.reasons == ()
    assert isinstance(decision, ApprovedPaperOrderGateDecision)
    assert decision.sized_order.quantity == 53
    assert decision.sized_order.planned_risk <= 75


@pytest.mark.parametrize(
    "clock",
    (
        PaperMarketClockSnapshot(
            observed_at=_at(9, 35, 59),
            market_timestamp=_at(9, 35, 59),
            is_open=True,
            next_open=_at(9, 30) + dt.timedelta(days=1),
            next_close=_at(16, 0),
        ),
        PaperMarketClockSnapshot(
            observed_at=_at(9, 36, 4),
            market_timestamp=_at(9, 36, 4),
            is_open=False,
            next_open=_at(9, 30) + dt.timedelta(days=1),
            next_close=_at(16, 0),
        ),
    ),
)
def test_gate_blocks_stale_or_closed_broker_clock(
    clock: PaperMarketClockSnapshot,
) -> None:
    decision = _evaluate(replace(_snapshot(), market_clock=clock))

    assert decision.state is PaperOrderGateState.SESSION_BLOCKED


@pytest.mark.parametrize(
    "evaluated_at",
    (
        dt.datetime(2026, 7, 18, 10, 0, tzinfo=NEW_YORK),
        _at(15, 30),
    ),
)
def test_gate_blocks_non_session_or_last_thirty_minutes(
    evaluated_at: dt.datetime,
) -> None:
    snapshot = replace(
        _snapshot(),
        market_clock=replace(
            _snapshot().market_clock,
            observed_at=evaluated_at,
            market_timestamp=evaluated_at,
        ),
        stream_heartbeat=replace(
            _snapshot().stream_heartbeat,
            authorized_at=evaluated_at - dt.timedelta(seconds=1),
            subscribed_at=evaluated_at - dt.timedelta(seconds=1),
            pong_at=evaluated_at,
        ),
        portfolio=replace(
            _snapshot().portfolio,
            observed_at=evaluated_at,
        ),
    )

    decision = _evaluate(snapshot, evaluated_at=evaluated_at)

    assert decision.state is PaperOrderGateState.SESSION_BLOCKED


@pytest.mark.parametrize(
    "bar",
    (
        LatestCompletedBar("AAPL", _at(9, 36), _at(9, 36, 2)),
        LatestCompletedBar("AAPL", _at(9, 34), _at(9, 36, 2)),
        LatestCompletedBar(
            "AAPL",
            dt.datetime(2026, 7, 13, 9, 35, tzinfo=NEW_YORK),
            _at(9, 36, 2),
        ),
        LatestCompletedBar("MSFT", _at(9, 35), _at(9, 36, 2)),
        LatestCompletedBar("AAPL", _at(9, 35), _at(9, 35, 59)),
    ),
)
def test_gate_requires_the_exact_just_completed_bar(
    bar: LatestCompletedBar,
) -> None:
    decision = _evaluate(replace(_snapshot(), latest_bar=bar))

    assert decision.state is PaperOrderGateState.CURRENT_BAR_BLOCKED


def test_gate_blocks_an_intent_created_before_the_bar_was_observed() -> None:
    snapshot = replace(
        _snapshot(),
        candidate_intent=_candidate(_at(9, 36, 1)),
    )

    decision = _evaluate(snapshot)

    assert decision.state is PaperOrderGateState.CURRENT_BAR_BLOCKED


def test_gate_does_not_treat_the_last_premarket_minute_as_a_regular_bar() -> None:
    evaluated_at = _at(9, 30, 5)
    snapshot = replace(
        _snapshot(),
        market_clock=replace(
            _snapshot().market_clock,
            observed_at=_at(9, 30, 4),
            market_timestamp=_at(9, 30, 4),
        ),
        latest_bar=LatestCompletedBar(
            symbol="AAPL",
            started_at=_at(9, 29),
            first_observed_at=_at(9, 30, 2),
        ),
        stream_heartbeat=replace(
            _snapshot().stream_heartbeat,
            authorized_at=_at(9, 30),
            subscribed_at=_at(9, 30),
            pong_at=_at(9, 30, 4),
        ),
        portfolio=replace(
            _snapshot().portfolio,
            observed_at=_at(9, 30, 4),
        ),
        candidate_intent=_candidate(_at(9, 30, 3)),
    )

    decision = _evaluate(snapshot, evaluated_at=evaluated_at)

    assert decision.state is PaperOrderGateState.CURRENT_BAR_BLOCKED


def test_gate_blocks_a_stale_stream() -> None:
    stale_stream = replace(
        _snapshot().stream_heartbeat,
        pong_at=_at(9, 35, 59),
    )
    stale_decision = _evaluate(
        replace(_snapshot(), stream_heartbeat=stale_stream)
    )

    assert stale_decision.state is PaperOrderGateState.STREAM_BLOCKED

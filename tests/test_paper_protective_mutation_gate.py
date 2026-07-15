from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from tests.paper_order_gate_fixtures import at, snapshot
from tests.paper_runtime_fixtures import account
from trading_agent.paper_execution_models import PaperBrokerState
from trading_agent.paper_order_gate_models import IncompletePaperPortfolio
from trading_agent.paper_protective_mutation_gate import (
    protective_mutation_readiness_reasons,
)
from trading_agent.paper_reconciliation import ReconciliationResult
from trading_agent.paper_runtime import PaperRuntimeReadiness


def _readiness() -> PaperRuntimeReadiness:
    gate = snapshot()
    return PaperRuntimeReadiness(
        broker_state=PaperBrokerState(account(), (), ()),
        market_clock=gate.market_clock,
        stream_heartbeat=gate.stream_heartbeat,
        reconciliation=ReconciliationResult(True, ()),
        portfolio=gate.portfolio,
    )


def test_protective_mutation_gate_accepts_current_regular_session_without_bar() -> None:
    assert (
        protective_mutation_readiness_reasons(
            _readiness(),
            at(9, 36, 5),
        )
        == ()
    )


@pytest.mark.parametrize(
    ("readiness", "now", "expected"),
    [
        (
            replace(_readiness(), runtime_reasons=("runtime-torn",)),
            at(9, 36, 5),
            "runtime-torn",
        ),
        (
            replace(
                _readiness(),
                reconciliation=ReconciliationResult(False, ("account-mismatch",)),
            ),
            at(9, 36, 5),
            "account-mismatch",
        ),
        (
            replace(
                _readiness(),
                portfolio=IncompletePaperPortfolio(("unknown-position",)),
            ),
            at(9, 36, 5),
            "unknown-position",
        ),
        (
            replace(
                _readiness(),
                market_clock=replace(_readiness().market_clock, is_open=False),
            ),
            at(9, 36, 5),
            "정규장",
        ),
        (
            replace(
                _readiness(),
                market_clock=replace(
                    _readiness().market_clock,
                    observed_at=at(9, 35, 59),
                    market_timestamp=at(9, 35, 59),
                ),
            ),
            at(9, 36, 5),
            "현재 5초",
        ),
        (
            replace(
                _readiness(),
                stream_heartbeat=replace(
                    _readiness().stream_heartbeat,
                    pong_at=at(9, 35, 59),
                ),
            ),
            at(9, 36, 5),
            "heartbeat",
        ),
        (
            replace(
                _readiness(),
                market_clock=replace(
                    _readiness().market_clock,
                    observed_at=at(15, 55),
                    market_timestamp=at(15, 55),
                ),
                stream_heartbeat=replace(
                    _readiness().stream_heartbeat,
                    pong_at=at(15, 55),
                ),
            ),
            at(15, 55),
            "평탄화",
        ),
    ],
)
def test_protective_mutation_gate_fails_closed(
    readiness: PaperRuntimeReadiness,
    now: dt.datetime,
    expected: str,
) -> None:
    reasons = protective_mutation_readiness_reasons(readiness, now)

    assert any(expected in reason for reason in reasons)

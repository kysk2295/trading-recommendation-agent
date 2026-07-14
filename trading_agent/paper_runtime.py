from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol, override

from trading_agent.alpaca_paper_client import AlpacaPaperClient
from trading_agent.alpaca_paper_config import (
    AlpacaPaperCredentials,
    create_alpaca_paper_read_client,
)
from trading_agent.alpaca_paper_order_stream import PaperOrderStreamHeartbeat
from trading_agent.paper_execution_models import (
    PaperBrokerState,
    PaperMarketClockSnapshot,
)
from trading_agent.paper_order_gate_models import (
    CompletePaperPortfolio,
    PaperPortfolioSnapshot,
)
from trading_agent.paper_reconciliation import ReconciliationResult

MAX_RUNTIME_RECEIPT_AGE = dt.timedelta(seconds=5)

type CredentialLoader = Callable[[], AlpacaPaperCredentials]
type PaperStateLoader = Callable[[AlpacaPaperCredentials], PaperBrokerState]


class PaperHeartbeatStream(Protocol):
    def heartbeat(self, timeout_seconds: float) -> PaperOrderStreamHeartbeat: ...


class PaperRuntimeEpochChangedError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "REST 대사 중 Alpaca paper 주문 스트림 연결 세대가 바뀌었습니다"


@dataclass(frozen=True, slots=True)
class PaperRuntimeReadiness:
    broker_state: PaperBrokerState
    market_clock: PaperMarketClockSnapshot
    stream_heartbeat: PaperOrderStreamHeartbeat
    reconciliation: ReconciliationResult
    portfolio: PaperPortfolioSnapshot
    runtime_reasons: tuple[str, ...] = ()
    protective_exit_reasons: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return (
            not self.runtime_reasons
            and not self.protective_exit_reasons
            and self.reconciliation.ready
            and isinstance(self.portfolio, CompletePaperPortfolio)
        )

    @property
    def reasons(self) -> tuple[str, ...]:
        if isinstance(self.portfolio, CompletePaperPortfolio):
            portfolio_reasons: tuple[str, ...] = ()
        else:
            portfolio_reasons = self.portfolio.reasons
        return tuple(
            sorted(
                set(
                    (
                        *self.runtime_reasons,
                        *self.protective_exit_reasons,
                        *self.reconciliation.reasons,
                        *portfolio_reasons,
                    )
                )
            )
        )


type PaperStateAndClockLoader = Callable[
    [AlpacaPaperCredentials],
    tuple[PaperBrokerState, PaperMarketClockSnapshot],
]
type PaperStreamOpener = Callable[
    [AlpacaPaperCredentials],
    AbstractContextManager[PaperHeartbeatStream],
]


def read_paper_broker_state(
    credentials: AlpacaPaperCredentials,
) -> PaperBrokerState:
    with create_alpaca_paper_read_client() as http_client:
        client = AlpacaPaperClient(http_client, credentials)
        account = client.account()
        inventory = client.open_order_inventory()
        return PaperBrokerState(
            account=account,
            open_orders=inventory.entry_orders,
            positions=client.positions(),
            protective_ocos=inventory.protective_ocos,
        )


def read_paper_broker_state_and_clock(
    credentials: AlpacaPaperCredentials,
) -> tuple[PaperBrokerState, PaperMarketClockSnapshot]:
    with create_alpaca_paper_read_client() as http_client:
        client = AlpacaPaperClient(http_client, credentials)
        inventory = client.open_order_inventory()
        broker_state = PaperBrokerState(
            account=client.account(),
            open_orders=inventory.entry_orders,
            positions=client.positions(),
            protective_ocos=inventory.protective_ocos,
        )
        return broker_state, client.clock()


def paper_runtime_receipt_reasons(
    before_rest: PaperOrderStreamHeartbeat,
    broker_state: PaperBrokerState,
    market_clock: PaperMarketClockSnapshot,
    after_rest: PaperOrderStreamHeartbeat,
) -> tuple[str, ...]:
    boundaries = (before_rest.pong_at, after_rest.pong_at)
    receipts = (
        broker_state.account.observed_at,
        market_clock.observed_at,
        *(snapshot.observed_at for snapshot in broker_state.protective_ocos),
    )
    if not all(_is_aware(value) for value in (*boundaries, *receipts)):
        return ("REST 응답 수신 시각이 timezone-aware 값이 아닙니다",)
    before, after = boundaries
    if before > after or any(
        not before <= receipt <= after or after - receipt > MAX_RUNTIME_RECEIPT_AGE for receipt in receipts
    ):
        return ("REST 응답 수신 시각이 현재 스트림 heartbeat 구간 밖입니다",)
    return ()


def _is_aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None

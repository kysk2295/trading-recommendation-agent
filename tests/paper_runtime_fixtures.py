from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal

from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_order_stream import (
    PaperOrderStreamHeartbeat,
    PaperStreamEpoch,
)
from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.execution_schema import StoredIntent
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerOrderId,
    IntentId,
    PaperAccountSnapshot,
    PaperBrokerState,
    PaperMarketClockSnapshot,
    PaperOrderIntent,
    PaperOrderSide,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
)
from trading_agent.paper_order_gate_models import LatestCompletedBar

FINGERPRINT = AccountFingerprint("a" * 64)
EXISTING_ID = IntentId("existing-msft")


class FakeReadyStream:
    def __init__(
        self,
        epochs: tuple[PaperStreamEpoch, PaperStreamEpoch] = (
            PaperStreamEpoch("epoch-1"),
            PaperStreamEpoch("epoch-1"),
        ),
    ) -> None:
        self.active = False
        self.heartbeat_count = 0
        self.epochs = epochs

    def heartbeat(self, timeout_seconds: float) -> PaperOrderStreamHeartbeat:
        assert self.active is True
        assert timeout_seconds == 5.0
        self.heartbeat_count += 1
        observed_at = dt.datetime(
            2026,
            7,
            14,
            13,
            36,
            self.heartbeat_count,
            tzinfo=dt.UTC,
        )
        return PaperOrderStreamHeartbeat(
            connection_epoch=self.epochs[self.heartbeat_count - 1],
            authorized_at=dt.datetime(2026, 7, 14, 13, 36, tzinfo=dt.UTC),
            subscribed_at=dt.datetime(2026, 7, 14, 13, 36, tzinfo=dt.UTC),
            pong_at=observed_at,
        )


class FakeLedgerReader:
    def __init__(
        self,
        stream: FakeReadyStream,
        ledger: ReconciliationLedger,
    ) -> None:
        self.stream = stream
        self.ledger = ledger
        self.read_count = 0

    def reconciliation_ledger(self) -> ReconciliationLedger:
        assert self.stream.active is True
        assert self.stream.heartbeat_count == 1
        self.read_count += 1
        return self.ledger


def account() -> PaperAccountSnapshot:
    return PaperAccountSnapshot(
        observed_at=dt.datetime(2026, 7, 14, 13, 36, 1, tzinfo=dt.UTC),
        status="ACTIVE",
        trading_blocked=False,
        equity=Decimal("30000"),
        last_equity=Decimal("30000"),
        buying_power=Decimal("60000"),
        account_fingerprint=FINGERPRINT,
    )


def market_clock() -> PaperMarketClockSnapshot:
    eastern = dt.timezone(dt.timedelta(hours=-4))
    return PaperMarketClockSnapshot(
        observed_at=dt.datetime(2026, 7, 14, 13, 36, 1, tzinfo=dt.UTC),
        market_timestamp=dt.datetime(2026, 7, 14, 9, 36, 1, tzinfo=eastern),
        is_open=True,
        next_open=dt.datetime(2026, 7, 15, 9, 30, tzinfo=eastern),
        next_close=dt.datetime(2026, 7, 14, 16, 0, tzinfo=eastern),
    )


def partial_state(*, include_position: bool = True) -> PaperBrokerState:
    order = PaperOrderSnapshot(
        broker_order_id=BrokerOrderId("order-1"),
        client_order_id=EXISTING_ID,
        symbol="MSFT",
        side=PaperOrderSide.BUY,
        status="partially_filled",
        quantity=Decimal(50),
        filled_quantity=Decimal(20),
        limit_price=Decimal("100"),
        time_in_force="day",
        extended_hours=False,
    )
    positions = (
        (PaperPositionSnapshot("MSFT", Decimal(20), Decimal("2020")),)
        if include_position
        else ()
    )
    return PaperBrokerState(account(), (order,), positions)


def ledger(*, with_existing: bool = False) -> ReconciliationLedger:
    existing = StoredIntent(
        intent_id=EXISTING_ID,
        strategy_id="orb",
        strategy_version="1",
        symbol="MSFT",
        created_at="2026-07-14T09:35:00-04:00",
        side=PaperOrderSide.BUY,
        entry_limit=Decimal("100"),
        stop=Decimal("99"),
        target_1r=Decimal("101"),
        target_2r=Decimal("102"),
        quantity=50,
    )
    return ReconciliationLedger(
        intents=(existing,) if with_existing else (),
        unresolved_intent_ids=(
            frozenset({EXISTING_ID}) if with_existing else frozenset()
        ),
        account_fingerprint=FINGERPRINT,
    )


def candidate() -> PaperOrderIntent:
    eastern = dt.timezone(dt.timedelta(hours=-4))
    return PaperOrderIntent(
        intent_id=IntentId("candidate-aapl"),
        strategy_id="orb",
        strategy_version="1",
        symbol="AAPL",
        created_at=dt.datetime(2026, 7, 14, 9, 36, 2, tzinfo=eastern),
        side=PaperOrderSide.BUY,
        entry_limit=100.0,
        stop=99.0,
        target_1r=101.0,
        target_2r=102.0,
    )


def latest_bar() -> LatestCompletedBar:
    eastern = dt.timezone(dt.timedelta(hours=-4))
    return LatestCompletedBar(
        symbol="AAPL",
        started_at=dt.datetime(2026, 7, 14, 9, 35, tzinfo=eastern),
        first_observed_at=dt.datetime(
            2026,
            7,
            14,
            9,
            36,
            1,
            tzinfo=eastern,
        ),
    )


def stream_opener(stream: FakeReadyStream):
    @contextmanager
    def opener(_: AlpacaPaperCredentials) -> Iterator[FakeReadyStream]:
        stream.active = True
        try:
            yield stream
        finally:
            stream.active = False

    return opener


def credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials("test-key", "test-secret")

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    trade_update,
)
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_order_stream import (
    PaperOrderStreamHeartbeat,
    PaperStreamEpoch,
    PaperTradeUpdateFrame,
    PaperTradeUpdateWireKind,
)
from trading_agent.paper_execution_models import (
    BrokerOrderId,
    IntentId,
    PaperAccountSnapshot,
    PaperBrokerState,
    PaperOrderSide,
    PaperOrderSnapshot,
)
from trading_agent.paper_stream_recovery_runtime import (
    PaperRecoveryState,
)


class TradeUpdateStream:
    connection_epoch = PaperStreamEpoch("epoch-from-stream")

    def __init__(
        self,
        frame: PaperTradeUpdateFrame | None = None,
        epochs: tuple[PaperStreamEpoch, ...] = (
            PaperStreamEpoch("epoch-from-stream"),
            PaperStreamEpoch("epoch-from-stream"),
        ),
    ) -> None:
        self.receive_count = 0
        self.heartbeat_count = 0
        self.epochs = epochs
        self.frame = frame or PaperTradeUpdateFrame(
            trade_update().payload_json.encode(),
            PaperTradeUpdateWireKind.BINARY,
        )

    def receive_trade_update_frame(
        self,
        timeout_seconds: float,
    ) -> PaperTradeUpdateFrame:
        assert timeout_seconds == 1.0
        self.receive_count += 1
        return self.frame

    def heartbeat(self, timeout_seconds: float) -> PaperOrderStreamHeartbeat:
        assert timeout_seconds == 5.0
        self.heartbeat_count += 1
        offset = self.heartbeat_count - 2
        return PaperOrderStreamHeartbeat(
            connection_epoch=self.epochs[
                min(self.heartbeat_count - 1, len(self.epochs) - 1)
            ],
            authorized_at=OBSERVED_AT - dt.timedelta(seconds=2),
            subscribed_at=OBSERVED_AT - dt.timedelta(seconds=2),
            pong_at=OBSERVED_AT + dt.timedelta(seconds=offset),
        )


def broker_state(
    observed_at: dt.datetime = OBSERVED_AT,
) -> PaperBrokerState:
    return PaperBrokerState(
        PaperAccountSnapshot(
            observed_at=observed_at,
            status="ACTIVE",
            trading_blocked=False,
            equity=Decimal(30_000),
            last_equity=Decimal(30_000),
            buying_power=Decimal(60_000),
            account_fingerprint=FINGERPRINT,
        ),
        (),
        (),
    )


def recovery_state(
    unresolved_intent_ids: frozenset[IntentId],
    observed_at: dt.datetime = OBSERVED_AT,
) -> PaperRecoveryState:
    targeted_orders = tuple(
        PaperOrderSnapshot(
            broker_order_id=BrokerOrderId("paper-order-1"),
            client_order_id=intent_id,
            symbol="AAA",
            side=PaperOrderSide.BUY,
            status="accepted",
            quantity=Decimal(100),
            filled_quantity=Decimal(0),
            limit_price=Decimal("10.00"),
            time_in_force="day",
            extended_hours=False,
        )
        for intent_id in sorted(unresolved_intent_ids)
    )
    return PaperRecoveryState(broker_state(observed_at), targeted_orders)


def state_loader(stream: TradeUpdateStream):
    def load(
        _: AlpacaPaperCredentials,
        unresolved: frozenset[IntentId],
    ) -> PaperRecoveryState:
        observed_at = OBSERVED_AT + dt.timedelta(
            seconds=stream.heartbeat_count - 1.5
        )
        return recovery_state(unresolved, observed_at)

    return load

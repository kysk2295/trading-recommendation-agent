from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import final

from websockets.exceptions import ConnectionClosed, InvalidHandshake

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_sip_dynamic_receipt_models import (
    AlpacaSipDynamicRawReceipt,
    AlpacaSipDynamicReceiptError,
    AlpacaSipDynamicReceiptKind,
    AlpacaSipDynamicTerminalStatus,
    StoredAlpacaSipDynamicReceipt,
)
from trading_agent.alpaca_sip_dynamic_receipt_sqlite import AlpacaSipDynamicConnectionLease
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_subscription import (
    AlpacaSipDynamicSubscriptionError,
    AlpacaSipDynamicSubscriptionPlan,
    dynamic_subscription_request_bytes,
    validate_dynamic_subscription_ack,
)
from trading_agent.alpaca_sip_dynamic_terminal_store import AlpacaSipDynamicTerminalStore
from trading_agent.alpaca_sip_trade_stream import (
    ALPACA_SIP_TRADE_STREAM_URL,
    AlpacaSipTradeStreamConnection,
    AlpacaSipTradeStreamConnector,
    connect_alpaca_sip_trade_stream,
)
from trading_agent.alpaca_sip_trade_stream_endpoint import require_alpaca_sip_trade_stream_url
from trading_agent.alpaca_sip_trade_stream_models import (
    AlpacaSipControlStage,
    AlpacaSipTradeStreamError,
    parse_alpaca_sip_control_frame,
)

_CONTROL_TIMEOUT_SECONDS = 5.0
_MAX_FRAME_BYTES = 1_048_576
_EPOCH = re.compile(r"^[0-9a-f]{32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicConnectionEvidence:
    plan_id: str
    connection_epoch: str
    bound_at: dt.datetime
    subscribed_at: dt.datetime
    completed_at: dt.datetime
    receipt_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            _HEX64.fullmatch(self.plan_id) is None
            or _EPOCH.fullmatch(self.connection_epoch) is None
            or not all(_aware(value) for value in (self.bound_at, self.subscribed_at, self.completed_at))
            or not self.bound_at <= self.subscribed_at <= self.completed_at
            or len(self.receipt_ids) < 4
            or self.receipt_ids != tuple(dict.fromkeys(self.receipt_ids))
            or any(_HEX64.fullmatch(receipt_id) is None for receipt_id in self.receipt_ids)
        ):
            raise AlpacaSipDynamicReceiptError


@final
class _ReceiptSession:
    __slots__ = ("_clock", "_connection", "_epoch", "_plan", "_receipts", "_store")

    def __init__(
        self,
        connection: AlpacaSipTradeStreamConnection,
        plan: AlpacaSipDynamicSubscriptionPlan,
        store: AlpacaSipDynamicReceiptStore,
        epoch: str,
        clock: Callable[[], dt.datetime],
    ) -> None:
        self._connection = connection
        self._plan = plan
        self._store = store
        self._epoch = epoch
        self._clock = clock
        self._receipts: list[StoredAlpacaSipDynamicReceipt] = []

    @property
    def receipts(self) -> tuple[StoredAlpacaSipDynamicReceipt, ...]:
        return tuple(self._receipts)

    def receive(self, kind: AlpacaSipDynamicReceiptKind, timeout_seconds: float) -> bytes:
        raw = self._connection.recv(timeout_seconds)
        payload = raw.encode() if isinstance(raw, str) else raw
        if type(payload) is not bytes or not payload or len(payload) > _MAX_FRAME_BYTES:
            raise AlpacaSipDynamicReceiptError
        stored = self._store.append_raw(
            self._plan,
            AlpacaSipDynamicRawReceipt(
                self._epoch,
                len(self._receipts) + 1,
                self._clock(),
                kind,
                payload,
            ),
        )
        self._receipts.append(stored)
        return stored.payload


def run_alpaca_sip_dynamic_connection(
    credentials: AlpacaCredentials,
    plan: AlpacaSipDynamicSubscriptionPlan,
    store: AlpacaSipDynamicReceiptStore,
    *,
    max_data_frames: int,
    timeout_seconds: float,
    connector: AlpacaSipTradeStreamConnector = connect_alpaca_sip_trade_stream,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    _epoch_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
) -> AlpacaSipDynamicConnectionEvidence:
    if (
        type(credentials) is not AlpacaCredentials
        or not credentials.key_id
        or not credentials.secret_key
        or type(store) is not AlpacaSipDynamicReceiptStore
        or type(max_data_frames) is not int
        or max_data_frames <= 0
        or type(timeout_seconds) is not float
        or timeout_seconds <= 0
    ):
        raise AlpacaSipDynamicReceiptError
    url = require_alpaca_sip_trade_stream_url(ALPACA_SIP_TRADE_STREAM_URL)
    with AlpacaSipDynamicConnectionLease(store.path):
        epoch = _epoch_factory()
        bound_at = _clock()
        store.bind_connection(epoch, plan, bound_at)
        terminals = AlpacaSipDynamicTerminalStore(store.path)
        try:
            with connector(url) as connection:
                _ = require_alpaca_sip_trade_stream_url(connection.final_url)
                session = _ReceiptSession(connection, plan, store, epoch, _clock)
                connected = session.receive(AlpacaSipDynamicReceiptKind.CONTROL, _CONTROL_TIMEOUT_SECONDS)
                parse_alpaca_sip_control_frame(connected, AlpacaSipControlStage.CONNECTED, plan.symbols[0])
                connection.send(
                    json.dumps(
                        {"action": "auth", "key": credentials.key_id, "secret": credentials.secret_key},
                        separators=(",", ":"),
                    )
                )
                authenticated = session.receive(AlpacaSipDynamicReceiptKind.CONTROL, _CONTROL_TIMEOUT_SECONDS)
                parse_alpaca_sip_control_frame(authenticated, AlpacaSipControlStage.AUTHENTICATED, plan.symbols[0])
                connection.send(dynamic_subscription_request_bytes(plan).decode())
                subscribed = session.receive(AlpacaSipDynamicReceiptKind.CONTROL, _CONTROL_TIMEOUT_SECONDS)
                validate_dynamic_subscription_ack(subscribed, plan)
                subscribed_at = session.receipts[-1].received_at
                for _ in range(max_data_frames):
                    _ = session.receive(AlpacaSipDynamicReceiptKind.DATA, timeout_seconds)
            receipts = session.receipts
        except (
            AlpacaSipDynamicReceiptError,
            AlpacaSipDynamicSubscriptionError,
            AlpacaSipTradeStreamError,
            ConnectionClosed,
            InvalidHandshake,
            OSError,
            TimeoutError,
        ):
            _ = terminals.append(plan, epoch, _clock(), AlpacaSipDynamicTerminalStatus.FAILED)
            raise
        _ = terminals.append(
            plan,
            epoch,
            receipts[-1].received_at,
            AlpacaSipDynamicTerminalStatus.BOUNDED_COMPLETE,
        )
    return AlpacaSipDynamicConnectionEvidence(
        plan.plan_id,
        epoch,
        bound_at,
        subscribed_at,
        receipts[-1].received_at,
        tuple(item.receipt_id for item in receipts),
    )


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "AlpacaSipDynamicConnectionEvidence",
    "run_alpaca_sip_dynamic_connection",
)

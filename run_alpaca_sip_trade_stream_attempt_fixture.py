#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11", "websockets>=15"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from websockets.exceptions import InvalidHandshake

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_sip_trade_store import AlpacaSipTradeHistoryStore
from trading_agent.alpaca_sip_trade_stream import (
    ALPACA_SIP_TRADE_STREAM_URL,
    AlpacaSipTradeStreamConfig,
    AlpacaSipTradeStreamError,
    AlpacaSipTradeStreamStores,
    open_alpaca_sip_trade_stream,
)
from trading_agent.alpaca_sip_trade_stream_store import AlpacaSipTradeStreamStore

_DATE = dt.date(2026, 7, 17)
_NOW = dt.datetime(2026, 7, 17, 14, 30, tzinfo=dt.UTC)
_ERROR = "Alpaca SIP trade stream attempt fixture failed"


class _ConnectionLimitConnection:
    __slots__ = ("final_url",)

    def __init__(self) -> None:
        self.final_url = ALPACA_SIP_TRADE_STREAM_URL

    def send(self, message: str) -> None:
        _ = message

    def recv(self, timeout: float | None = None) -> str | bytes:
        _ = timeout
        return b'[{"T":"error","code":406,"msg":"connection limit exceeded"}]'


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record a local sanitized Alpaca SIP connection failure without network or credentials."
    )
    parser.add_argument(
        "--scenario",
        required=True,
        choices=("connection-limit", "handshake-failure"),
    )
    parser.add_argument("--stream-store", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = AlpacaSipTradeStreamConfig(_DATE, "AAPL")
    controls = AlpacaSipTradeStreamStore(args.stream_store)
    stores = AlpacaSipTradeStreamStores(
        controls,
        AlpacaSipTradeHistoryStore(args.stream_store.with_name("fixture-trades.sqlite3")),
    )
    connector = _connection_limit_connector if args.scenario == "connection-limit" else _handshake_failure_connector
    failed = False
    try:
        with open_alpaca_sip_trade_stream(
            AlpacaCredentials("local-fixture", "local-fixture"),
            config,
            stores,
            connector=connector,
            _clock=iter(_times(4)).__next__,
        ):
            pass
    except AlpacaSipTradeStreamError:
        failed = True
    if not failed:
        print(_ERROR, file=sys.stderr)
        return 2
    try:
        attempts = controls.load_connection_attempts(config)
        if len(attempts) != 1:
            raise AlpacaSipTradeStreamError
        attempt = attempts[0]
        if controls.load_terminal_status(attempt.connection_epoch) is not None:
            raise AlpacaSipTradeStreamError
    except (AlpacaSipTradeStreamError, OSError, TypeError, ValueError):
        print(_ERROR, file=sys.stderr)
        return 2
    summary = {
        "attempt_count": 1,
        "control_count": controls.control_count(),
        "failure_code": attempt.failure_code.value,
        "network_request_count": 0,
        "stage": attempt.stage.value,
        "terminal_session_count": 0,
    }
    print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
    return 0


@contextmanager
def _connection_limit_connector(_: str) -> Iterator[_ConnectionLimitConnection]:
    yield _ConnectionLimitConnection()


@contextmanager
def _handshake_failure_connector(_: str) -> Iterator[_ConnectionLimitConnection]:
    raise InvalidHandshake
    yield _ConnectionLimitConnection()


def _times(count: int) -> tuple[dt.datetime, ...]:
    return tuple(_NOW + dt.timedelta(milliseconds=index) for index in range(count))


if __name__ == "__main__":
    raise SystemExit(main())

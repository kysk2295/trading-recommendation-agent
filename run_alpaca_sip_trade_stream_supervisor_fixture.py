#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11", "websockets>=15"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import stat
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
from trading_agent.alpaca_sip_trade_stream_supervisor import (
    AlpacaSipReconnectPolicy,
    AlpacaSipTradeStreamSupervisorError,
    run_alpaca_sip_trade_stream_supervisor,
)

_DATE = dt.date(2026, 7, 17)
_NOW = dt.datetime(2026, 7, 17, 14, 30, tzinfo=dt.UTC)
_ERROR = "Alpaca SIP trade stream supervisor fixture failed"


class _ReadyConnection:
    __slots__ = ("_responses", "final_url")

    def __init__(self) -> None:
        self._responses = [_connected(), _authenticated(), _subscribed(), _trade()]
        self.final_url = ALPACA_SIP_TRADE_STREAM_URL

    def send(self, message: str) -> None:
        _ = message

    def recv(self, timeout: float | None = None) -> str | bytes:
        _ = timeout
        return self._responses.pop(0)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a local bounded Alpaca SIP reconnect supervisor without network or credentials."
    )
    parser.add_argument("--state-dir", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        state_dir = _private_state_dir(args.state_dir)
        config = AlpacaSipTradeStreamConfig(_DATE, "AAPL")
        controls = AlpacaSipTradeStreamStore(state_dir / "stream.sqlite3")
        stores = AlpacaSipTradeStreamStores(
            controls,
            AlpacaSipTradeHistoryStore(state_dir / "trades.sqlite3"),
        )
        calls = 0

        def operation() -> str:
            nonlocal calls
            calls += 1
            connector = _handshake_failure_connector if calls == 1 else _ready_connector
            with open_alpaca_sip_trade_stream(
                AlpacaCredentials("local-fixture", "local-fixture"),
                config,
                stores,
                connector=connector,
                _clock=iter(_times(8, offset=dt.timedelta(seconds=calls - 1))).__next__,
            ) as stream:
                _ = stream.receive_trade_frame(1.0)
                return stream.connection_epoch

        sleeps: list[float] = []
        result = run_alpaca_sip_trade_stream_supervisor(
            operation,
            config,
            controls,
            AlpacaSipReconnectPolicy(3, 1.0),
            sleeper=sleeps.append,
        )
        summary = {
            "attempt_count": len(controls.load_connection_attempts(config)),
            "continuity_attested": result.continuity_attested,
            "network_request_count": 0,
            "operation_count": result.operation_count,
            "sleep_seconds": sleeps,
            "status": result.status.value,
            "terminal_session_count": len(controls.load_session_history(config)),
            "total_connection_count": result.total_connection_count,
        }
    except (AlpacaSipTradeStreamError, AlpacaSipTradeStreamSupervisorError, OSError, TypeError, ValueError):
        print(_ERROR, file=sys.stderr)
        return 2
    print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
    return 0


def _private_state_dir(path: Path) -> Path:
    candidate = path.expanduser().absolute()
    candidate.mkdir(parents=True, mode=0o700, exist_ok=True)
    metadata = candidate.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise AlpacaSipTradeStreamSupervisorError
    return candidate


@contextmanager
def _ready_connector(_: str) -> Iterator[_ReadyConnection]:
    yield _ReadyConnection()


@contextmanager
def _handshake_failure_connector(_: str) -> Iterator[_ReadyConnection]:
    raise InvalidHandshake
    yield _ReadyConnection()


def _times(
    count: int,
    *,
    offset: dt.timedelta,
) -> tuple[dt.datetime, ...]:
    return tuple(_NOW + offset + dt.timedelta(milliseconds=index) for index in range(count))


def _connected() -> bytes:
    return b'[{"T":"success","msg":"connected"}]'


def _authenticated() -> bytes:
    return b'[{"T":"success","msg":"authenticated"}]'


def _subscribed() -> bytes:
    return (
        b'[{"T":"subscription","trades":["AAPL"],"quotes":[],"bars":[],"updatedBars":[],'
        b'"dailyBars":[],"statuses":[],"lulds":[],"corrections":["AAPL"],'
        b'"cancelErrors":["AAPL"]}]'
    )


def _trade() -> bytes:
    return (
        b'[{"T":"t","S":"AAPL","i":101,"x":"V","p":211.25,"s":40,"c":["@"],"t":"2026-07-17T14:29:59.123456Z","z":"C"}]'
    )


if __name__ == "__main__":
    raise SystemExit(main())

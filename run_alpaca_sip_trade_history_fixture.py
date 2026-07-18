#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["duckdb==1.5.4", "pyarrow==25.0.0", "pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from pydantic import ValidationError

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_sip_trade_fixture_models import (
    AlpacaSipTradeFixtureError,
    AlpacaSipTradeHistoryFixture,
)
from trading_agent.alpaca_sip_trade_history import (
    AlpacaSipTradeHistoryError,
    AlpacaSipTradeHistoryRequest,
    AlpacaSipTradeInstrumentBinding,
    project_alpaca_sip_trade_history,
)
from trading_agent.alpaca_sip_trade_history_coverage import (
    assess_alpaca_sip_bounded_trade_history_coverage,
    assess_alpaca_sip_trade_history_coverage,
)
from trading_agent.alpaca_sip_trade_store import AlpacaSipTradeHistoryStore
from trading_agent.alpaca_sip_trade_stream import (
    ALPACA_SIP_TRADE_STREAM_URL,
    AlpacaSipTradeStreamConfig,
    AlpacaSipTradeStreamError,
    AlpacaSipTradeStreamStores,
    open_alpaca_sip_trade_stream,
)
from trading_agent.alpaca_sip_trade_stream_store import AlpacaSipTradeStreamStore
from trading_agent.canonical_event_history import active_canonical_events_as_of
from trading_agent.canonical_event_models import CanonicalEventOperation
from trading_agent.canonical_parquet_writer import (
    CanonicalDatasetParquetWriterError,
    write_canonical_dataset_parquet,
)

_INPUT_ERROR = "Alpaca SIP trade history fixture is invalid"
_PROJECTION_ERROR = "Alpaca SIP trade history fixture projection failed"


class _FixtureStreamConnection:
    __slots__ = ("_responses", "final_url")

    def __init__(self, responses: list[bytes]) -> None:
        self._responses = responses
        self.final_url = ALPACA_SIP_TRADE_STREAM_URL

    def send(self, message: str) -> None:
        _ = message

    def recv(self, timeout: float | None = None) -> str | bytes:
        _ = timeout
        return self._responses.pop(0)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Project local Alpaca SIP t/c/x fixtures without network, credentials, or broker access."
    )
    parser.add_argument("--input", required=True, type=Path, help="local fixture JSON")
    parser.add_argument("--store", required=True, type=Path, help="private raw-frame SQLite path")
    parser.add_argument("--stream-store", type=Path, help="optional private stream audit SQLite path")
    parser.add_argument("--output-root", required=True, type=Path, help="private canonical dataset root")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        fixture = AlpacaSipTradeHistoryFixture.model_validate_json(args.input.read_bytes())
    except (AlpacaSipTradeFixtureError, OSError, ValidationError, ValueError):
        print(_INPUT_ERROR, file=sys.stderr)
        return 1
    try:
        store = AlpacaSipTradeHistoryStore(args.store)
        stream_summary: dict[str, int] = {}
        if args.stream_store is None:
            frames = tuple(store.append_frame(frame.to_received_frame(fixture.market_date)) for frame in fixture.frames)
            attestation = None
        else:
            controls = AlpacaSipTradeStreamStore(args.stream_store)
            received_frames = tuple(frame.to_received_frame(fixture.market_date) for frame in fixture.frames)
            connection = _FixtureStreamConnection(
                [_connected(), _authenticated(), _subscribed(fixture.symbol)]
                + [frame.payload for frame in received_frames]
            )
            first_received = received_frames[0].received_at
            times = iter(
                (
                    first_received - dt.timedelta(microseconds=3),
                    first_received - dt.timedelta(microseconds=2),
                    first_received - dt.timedelta(microseconds=1),
                    *(frame.received_at for frame in received_frames),
                    received_frames[-1].received_at + dt.timedelta(microseconds=1),
                )
            )
            with open_alpaca_sip_trade_stream(
                AlpacaCredentials("local-fixture", "local-fixture"),
                AlpacaSipTradeStreamConfig(fixture.market_date, fixture.symbol),
                AlpacaSipTradeStreamStores(controls, store),
                connector=_fixture_connector(connection),
                _clock=times.__next__,
            ) as stream:
                frames = tuple(stream.receive_trade_frame(1.0) for _ in fixture.frames)
                epoch = stream.connection_epoch
            attestation = controls.load_attestation(epoch)
            if attestation is None:
                raise AlpacaSipTradeStreamError
            stream_summary = {
                "stream_control_count": controls.control_count(),
                "stream_data_link_count": controls.data_link_count(epoch),
            }
        batch = project_alpaca_sip_trade_history(
            frames,
            AlpacaSipTradeHistoryRequest(
                fixture.market_date,
                (AlpacaSipTradeInstrumentBinding(fixture.symbol, fixture.instrument_id),),
            ),
        )
        publication = write_canonical_dataset_parquet(batch, output_root=args.output_root)
        as_of = max(event.normalized_at for event in batch.events)
        active = active_canonical_events_as_of(batch.events, as_of=as_of)
        coverage = (
            assess_alpaca_sip_trade_history_coverage(batch)
            if attestation is None
            else assess_alpaca_sip_bounded_trade_history_coverage(batch, attestation)
        )
    except (
        AlpacaSipTradeFixtureError,
        AlpacaSipTradeHistoryError,
        AlpacaSipTradeStreamError,
        CanonicalDatasetParquetWriterError,
        OSError,
        TypeError,
        ValueError,
    ):
        print(_PROJECTION_ERROR, file=sys.stderr)
        return 2
    summary = {
        "active_trade_count": len(active),
        "correction_count": sum(event.operation is CanonicalEventOperation.CORRECTION for event in batch.events),
        "dataset_id": publication.dataset_id,
        "event_count": len(batch.events),
        "history_complete": coverage.complete_history,
        "history_reason_codes": list(coverage.reason_codes),
        "network_request_count": 0,
        "raw_frame_count": len(frames),
        "tombstone_count": sum(event.operation is CanonicalEventOperation.TOMBSTONE for event in batch.events),
    }
    summary.update(stream_summary)
    print(json.dumps(summary, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0


def _fixture_connector(
    connection: _FixtureStreamConnection,
) -> Callable[[str], AbstractContextManager[_FixtureStreamConnection]]:
    @contextmanager
    def connector(_: str) -> Iterator[_FixtureStreamConnection]:
        yield connection

    return connector


def _connected() -> bytes:
    return b'[{"T":"success","msg":"connected"}]'


def _authenticated() -> bytes:
    return b'[{"T":"success","msg":"authenticated"}]'


def _subscribed(symbol: str) -> bytes:
    message = {
        "T": "subscription",
        "bars": [],
        "cancelErrors": [symbol],
        "corrections": [symbol],
        "dailyBars": [],
        "lulds": [],
        "quotes": [],
        "statuses": [],
        "trades": [symbol],
        "updatedBars": [],
    }
    return json.dumps((message,), separators=(",", ":"), sort_keys=True).encode()


if __name__ == "__main__":
    raise SystemExit(main())

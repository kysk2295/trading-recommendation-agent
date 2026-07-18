#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["duckdb==1.5.4", "pyarrow==25.0.0", "pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

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
from trading_agent.alpaca_sip_trade_history_coverage import assess_alpaca_sip_trade_history_coverage
from trading_agent.alpaca_sip_trade_store import AlpacaSipTradeHistoryStore
from trading_agent.canonical_event_history import active_canonical_events_as_of
from trading_agent.canonical_event_models import CanonicalEventOperation
from trading_agent.canonical_parquet_writer import (
    CanonicalDatasetParquetWriterError,
    write_canonical_dataset_parquet,
)

_INPUT_ERROR = "Alpaca SIP trade history fixture is invalid"
_PROJECTION_ERROR = "Alpaca SIP trade history fixture projection failed"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Project local Alpaca SIP t/c/x fixtures without network, credentials, or broker access."
    )
    parser.add_argument("--input", required=True, type=Path, help="local fixture JSON")
    parser.add_argument("--store", required=True, type=Path, help="private raw-frame SQLite path")
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
        frames = tuple(store.append_frame(frame.to_received_frame(fixture.market_date)) for frame in fixture.frames)
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
        coverage = assess_alpaca_sip_trade_history_coverage(batch)
    except (
        AlpacaSipTradeFixtureError,
        AlpacaSipTradeHistoryError,
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
    print(json.dumps(summary, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

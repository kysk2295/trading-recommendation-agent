#!/usr/bin/env -S uv run --python 3.12 python

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import stat
import sys
from collections.abc import Sequence
from pathlib import Path

from trading_agent.alpaca_http import (
    DEFAULT_ALPACA_SECRET_PATH,
    AlpacaCredentials,
    AlpacaSecretFileError,
    MissingAlpacaCredentialsError,
    load_alpaca_credentials,
)
from trading_agent.alpaca_sip_trade_history import (
    AlpacaSipTradeHistoryError,
    AlpacaSipTradeHistoryRequest,
    AlpacaSipTradeInstrumentBinding,
    project_alpaca_sip_trade_history,
)
from trading_agent.alpaca_sip_trade_history_coverage import (
    assess_alpaca_sip_bounded_trade_history_coverage,
)
from trading_agent.alpaca_sip_trade_store import AlpacaSipTradeHistoryStore
from trading_agent.alpaca_sip_trade_stream import (
    AlpacaSipTradeStreamConfig,
    AlpacaSipTradeStreamError,
    AlpacaSipTradeStreamStores,
    connect_alpaca_sip_trade_stream,
    open_alpaca_sip_trade_stream,
)
from trading_agent.alpaca_sip_trade_stream_store import AlpacaSipTradeStreamStore
from trading_agent.canonical_event_models import CanonicalEventOperation
from trading_agent.canonical_history_coverage import (
    CanonicalHistoryCoverageError,
    require_complete_canonical_history,
)
from trading_agent.canonical_parquet_writer import (
    CanonicalDatasetParquetWriterError,
    write_canonical_dataset_parquet,
)
from trading_agent.kis_live import regular_session_is_open
from trading_agent.private_report import write_private_report
from trading_agent.us_equity_calendar import NEW_YORK

REPORT_NAME = "alpaca-sip-trade-stream-smoke.json"
_BLOCKED = "Alpaca SIP trade stream smoke is blocked"
_FAILED = "Alpaca SIP trade stream smoke failed"


class AlpacaSipTradeStreamSmokeError(RuntimeError):
    def __str__(self) -> str:
        return _BLOCKED


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one bounded read-only Alpaca SIP trade stream smoke.")
    parser.add_argument("--instrument-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--secret-path", type=Path, default=DEFAULT_ALPACA_SECRET_PATH)
    parser.add_argument("--max-frames", type=int, choices=range(1, 11), default=1)
    parser.add_argument("--receive-timeout-seconds", type=float, default=5.0)
    parser.add_argument("--arm-read-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if not args.arm_read_only or not 0 < args.receive_timeout_seconds <= 10:
            raise AlpacaSipTradeStreamSmokeError
        observed_at = _utc_now()
        if not regular_session_is_open(observed_at):
            raise AlpacaSipTradeStreamSmokeError
        market_date = observed_at.astimezone(NEW_YORK).date()
        config = AlpacaSipTradeStreamConfig(market_date, args.symbol)
        request = AlpacaSipTradeHistoryRequest(
            market_date,
            (AlpacaSipTradeInstrumentBinding(args.symbol, args.instrument_id),),
        )
    except (
        AlpacaSipTradeHistoryError,
        AlpacaSipTradeStreamError,
        AlpacaSipTradeStreamSmokeError,
        TypeError,
        ValueError,
    ):
        print(_BLOCKED, file=sys.stderr)
        return 1
    try:
        credentials = _load_private_credentials(args.secret_path)
        state_dir = _prepare_private_state_dir(args.state_dir)
        controls = AlpacaSipTradeStreamStore(state_dir / "stream.sqlite3")
        trades = AlpacaSipTradeHistoryStore(state_dir / "trades.sqlite3")
        with open_alpaca_sip_trade_stream(
            credentials,
            config,
            AlpacaSipTradeStreamStores(controls, trades),
            connector=connect_alpaca_sip_trade_stream,
            _clock=_utc_now,
        ) as stream:
            received = []
            for _ in range(args.max_frames):
                frame = stream.receive_trade_frame(args.receive_timeout_seconds)
                current = _utc_now()
                if not regular_session_is_open(current) or current.astimezone(NEW_YORK).date() != market_date:
                    raise AlpacaSipTradeStreamError
                received.append(frame)
            frames = tuple(received)
            epoch = stream.connection_epoch
        attestation = controls.load_attestation(epoch)
        if attestation is None:
            raise AlpacaSipTradeStreamSmokeError
        batch = project_alpaca_sip_trade_history(frames, request)
        coverage = require_complete_canonical_history(
            assess_alpaca_sip_bounded_trade_history_coverage(batch, attestation)
        )
        publication = write_canonical_dataset_parquet(
            batch,
            output_root=state_dir / "canonical",
        )
        summary = {
            "broker_mutation_count": 0,
            "correction_count": sum(event.operation is CanonicalEventOperation.CORRECTION for event in batch.events),
            "dataset_id": publication.dataset_id,
            "event_count": len(batch.events),
            "frame_count": len(frames),
            "history_complete": coverage.complete_history,
            "market_date": market_date.isoformat(),
            "stream_control_count": controls.control_count_for_epoch(epoch),
            "stream_data_link_count": controls.data_link_count(epoch),
            "symbol": config.symbol,
            "tombstone_count": sum(event.operation is CanonicalEventOperation.TOMBSTONE for event in batch.events),
            "websocket_connection_count": 1,
        }
        encoded = json.dumps(summary, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        write_private_report(state_dir / REPORT_NAME, encoded + "\n")
    except (
        AlpacaSecretFileError,
        AlpacaSipTradeHistoryError,
        AlpacaSipTradeStreamError,
        AlpacaSipTradeStreamSmokeError,
        CanonicalDatasetParquetWriterError,
        CanonicalHistoryCoverageError,
        MissingAlpacaCredentialsError,
        OSError,
        TypeError,
        ValueError,
    ):
        print(_FAILED, file=sys.stderr)
        return 2
    print(encoded)
    return 0


def _load_private_credentials(path: Path) -> AlpacaCredentials:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise AlpacaSipTradeStreamSmokeError
    return load_alpaca_credentials(path)


def _prepare_private_state_dir(path: Path) -> Path:
    candidate = path.expanduser().absolute()
    candidate.mkdir(parents=True, mode=0o700, exist_ok=True)
    metadata = candidate.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise AlpacaSipTradeStreamSmokeError
    return candidate


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


if __name__ == "__main__":
    raise SystemExit(main())

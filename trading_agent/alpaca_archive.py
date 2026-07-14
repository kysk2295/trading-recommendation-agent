from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path

import httpx2
from pydantic import ValidationError

from trading_agent.alpaca_bars import AlpacaBarsClient, AlpacaPageRequest
from trading_agent.alpaca_http import (
    AlpacaApiError,
    AlpacaCredentials,
    AlpacaMemoryLimitError,
    peak_rss_gib,
)
from trading_agent.alpaca_models import (
    CHECKPOINT_ADAPTER,
    CSV_HEADER,
    FULL_SESSION_WINDOW,
    AlpacaArchiveResult,
    AlpacaBarWindow,
    BatchCheckpoint,
)


class AlpacaMinuteArchive:
    def __init__(
        self,
        client: httpx2.Client,
        credentials: AlpacaCredentials,
        output_dir: Path,
        batch_size: int,
        rss_limit_gib: float = 10.0,
        request_interval_seconds: float = 0.31,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._credentials = credentials
        self._output_dir = output_dir
        self._batch_size = batch_size
        self._rss_limit_gib = rss_limit_gib
        self._bars_client = AlpacaBarsClient(
            client=client,
            credentials=credentials,
            request_interval_seconds=request_interval_seconds,
            monotonic=monotonic,
            sleeper=sleeper,
        )

    def archive_session(
        self,
        session_date: dt.date,
        symbols: tuple[str, ...],
        *,
        window: AlpacaBarWindow = FULL_SESSION_WINDOW,
    ) -> AlpacaArchiveResult:
        normalized_symbols = tuple(sorted(set(symbols)))
        archive_id = self._archive_id(normalized_symbols, window)
        batches = tuple(
            normalized_symbols[offset : offset + self._batch_size]
            for offset in range(0, len(normalized_symbols), self._batch_size)
        )
        bar_count = 0
        request_count = 0
        new_request_count = 0
        skipped = 0
        for index, batch in enumerate(batches):
            checkpoint = self._completed_checkpoint(session_date, archive_id, index, batch, window)
            if checkpoint is not None:
                bar_count += checkpoint.bar_count
                request_count += checkpoint.request_count
                skipped += 1
                continue
            batch_bars, batch_requests = self._archive_batch(session_date, archive_id, index, batch, window)
            bar_count += batch_bars
            request_count += batch_requests
            new_request_count += batch_requests
        result = AlpacaArchiveResult(
            session_date=session_date,
            archive_dir=self._session_dir(session_date, archive_id),
            batch_count=len(batches),
            skipped_batch_count=skipped,
            bar_count=bar_count,
            request_count=request_count,
            new_request_count=new_request_count,
        )
        session_dir = self._session_dir(session_date, archive_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        session_metadata = session_dir / "session.metadata.json"
        temporary_metadata = session_dir / "session.metadata.json.tmp"
        temporary_metadata.write_text(
            json.dumps(
                {
                    "status": "complete",
                    "session_date": session_date.isoformat(),
                    "batch_count": result.batch_count,
                    "skipped_batch_count": result.skipped_batch_count,
                    "bar_count": result.bar_count,
                    "request_count": result.request_count,
                    "new_request_count": result.new_request_count,
                    "symbol_count": len(normalized_symbols),
                    "archive_id": archive_id,
                    "feed": "sip",
                    "timeframe": "1Min",
                    "adjustment": "raw",
                    "window_start": window.start.isoformat(),
                    "window_end": window.end.isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary_metadata.replace(session_metadata)
        return result

    def _completed_checkpoint(
        self,
        session_date: dt.date,
        archive_id: str,
        index: int,
        symbols: tuple[str, ...],
        window: AlpacaBarWindow,
    ) -> BatchCheckpoint | None:
        session_dir = self._session_dir(session_date, archive_id)
        data_path = session_dir / f"batch_{index:05d}.csv.gz"
        metadata_path = session_dir / f"batch_{index:05d}.metadata.json"
        if not data_path.is_file() or not metadata_path.is_file():
            return None
        try:
            checkpoint = CHECKPOINT_ADAPTER.validate_json(metadata_path.read_text(encoding="utf-8"))
        except ValidationError:
            return None
        complete = (
            checkpoint.status == "complete"
            and checkpoint.session_date == session_date
            and checkpoint.symbols == symbols
            and checkpoint.feed == "sip"
            and checkpoint.window_start == window.start
            and checkpoint.window_end == window.end
        )
        return checkpoint if complete else None

    def _archive_batch(
        self,
        session_date: dt.date,
        archive_id: str,
        index: int,
        symbols: tuple[str, ...],
        window: AlpacaBarWindow,
    ) -> tuple[int, int]:
        session_dir = self._session_dir(session_date, archive_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        data_path = session_dir / f"batch_{index:05d}.csv.gz"
        temporary_path = session_dir / f"batch_{index:05d}.csv.gz.part"
        page_token: str | None = None
        seen_tokens: set[str] = set()
        bar_count = 0
        request_count = 0
        with gzip.open(temporary_path, "wt", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(CSV_HEADER)
            while True:
                payload = self._bars_client.fetch_page(
                    AlpacaPageRequest(
                        session_date=session_date,
                        symbols=symbols,
                        window=window,
                        page_token=page_token,
                    )
                )
                request_count += 1
                rss_gib = peak_rss_gib()
                if rss_gib >= self._rss_limit_gib:
                    raise AlpacaMemoryLimitError(
                        rss_gib=rss_gib,
                        limit_gib=self._rss_limit_gib,
                    )
                for symbol, bars in payload.bars.items():
                    for bar in bars:
                        writer.writerow(
                            (
                                symbol,
                                bar.timestamp.isoformat(),
                                bar.open,
                                bar.high,
                                bar.low,
                                bar.close,
                                bar.volume,
                                bar.trade_count,
                                "" if bar.vwap is None else bar.vwap,
                            )
                        )
                        bar_count += 1
                page_token = payload.next_page_token
                if page_token is None:
                    break
                if page_token in seen_tokens:
                    raise AlpacaApiError(status_code=500, message="반복된 page token")
                seen_tokens.add(page_token)
        temporary_path.replace(data_path)
        checkpoint = BatchCheckpoint(
            status="complete",
            session_date=session_date,
            bar_count=bar_count,
            request_count=request_count,
            symbols=symbols,
            feed="sip",
            window_start=window.start,
            window_end=window.end,
        )
        metadata_path = session_dir / f"batch_{index:05d}.metadata.json"
        temporary_metadata = metadata_path.with_suffix(".json.tmp")
        temporary_metadata.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
        temporary_metadata.replace(metadata_path)
        return bar_count, request_count

    def _archive_id(self, symbols: tuple[str, ...], window: AlpacaBarWindow) -> str:
        fingerprint = f"{self._batch_size}\n{'\n'.join(symbols)}"
        if window != FULL_SESSION_WINDOW:
            fingerprint = f"{window.start.isoformat()}\n{window.end.isoformat()}\n{fingerprint}"
        return hashlib.sha256(fingerprint.encode()).hexdigest()[:16]

    def _session_dir(self, session_date: dt.date, archive_id: str) -> Path:
        return self._output_dir / session_date.strftime("%Y/%m/%d") / f"archive_{archive_id}"

from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from trading_agent.alpaca_bars import AlpacaBarsClient, AlpacaDailyPageRequest
from trading_agent.alpaca_daily_store import (
    complete_batch,
    completed_batch,
    finalize_cache,
    initialize_cache,
    insert_bars,
    load_references,
)
from trading_agent.alpaca_http import AlpacaApiError, AlpacaMemoryLimitError, peak_rss_gib
from trading_agent.alpaca_reference import AlpacaDailyReference


@dataclass(frozen=True, slots=True)
class AlpacaDailyCacheResult:
    database_path: Path
    batch_count: int
    skipped_batch_count: int
    request_count: int
    new_request_count: int
    bar_count: int


class AlpacaDailyRangeCache:
    def __init__(
        self,
        bars_client: AlpacaBarsClient,
        output_dir: Path,
        batch_size: int,
        lookback_calendar_days: int,
        reference_sessions: int,
        minimum_reference_sessions: int,
        rss_limit_gib: float = 10.0,
    ) -> None:
        if batch_size <= 0 or lookback_calendar_days < reference_sessions:
            raise ValueError("일봉 캐시 배치와 조회기간이 올바르지 않습니다")
        if not 0 < minimum_reference_sessions <= reference_sessions:
            raise ValueError("일봉 캐시 최소 이력이 참조 세션 수보다 큽니다")
        self._bars_client = bars_client
        self._output_dir = output_dir
        self._batch_size = batch_size
        self._lookback_calendar_days = lookback_calendar_days
        self._reference_sessions = reference_sessions
        self._minimum_reference_sessions = minimum_reference_sessions
        self._rss_limit_gib = rss_limit_gib
        self._database_path: Path | None = None

    def build(
        self,
        start_date: dt.date,
        end_date: dt.date,
        symbols: tuple[str, ...],
    ) -> AlpacaDailyCacheResult:
        if end_date < start_date:
            raise ValueError("일봉 캐시 종료일은 시작일보다 빠를 수 없습니다")
        normalized = tuple(sorted(set(symbols)))
        database_path = self._cache_path(start_date, end_date, normalized)
        self._database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        batches = tuple(
            normalized[offset : offset + self._batch_size] for offset in range(0, len(normalized), self._batch_size)
        )
        skipped = 0
        requests = 0
        new_requests = 0
        with sqlite3.connect(database_path) as connection:
            initialize_cache(connection, start_date, end_date, len(normalized))
            for index, batch in enumerate(batches):
                checkpoint = completed_batch(connection, index, batch)
                if checkpoint is not None:
                    skipped += 1
                    requests += checkpoint
                    continue
                batch_requests = self._archive_batch(connection, start_date, end_date, index, batch)
                requests += batch_requests
                new_requests += batch_requests
            bar_count = finalize_cache(connection, requests)
        return AlpacaDailyCacheResult(
            database_path=database_path,
            batch_count=len(batches),
            skipped_batch_count=skipped,
            request_count=requests,
            new_request_count=new_requests,
            bar_count=bar_count,
        )

    def references_for_session(
        self,
        session_date: dt.date,
        symbols: tuple[str, ...],
    ) -> tuple[AlpacaDailyReference, ...]:
        if self._database_path is None:
            raise RuntimeError("일봉 범위 캐시를 먼저 build해야 합니다")
        return load_references(
            self._database_path,
            session_date,
            symbols,
            self._lookback_calendar_days,
            self._reference_sessions,
            self._minimum_reference_sessions,
        )

    def _archive_batch(
        self,
        connection: sqlite3.Connection,
        start_date: dt.date,
        end_date: dt.date,
        index: int,
        symbols: tuple[str, ...],
    ) -> int:
        first_date = start_date - dt.timedelta(days=self._lookback_calendar_days)
        last_date = end_date - dt.timedelta(days=1)
        page_token: str | None = None
        seen_tokens: set[str] = set()
        request_count = 0
        bar_count = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            while True:
                payload = self._bars_client.fetch_daily_page(
                    AlpacaDailyPageRequest(
                        session_date=end_date,
                        symbols=symbols,
                        start_date=first_date,
                        end_date=last_date,
                        page_token=page_token,
                    )
                )
                request_count += 1
                rows = (
                    (symbol, bar_date.isoformat(), bar.close, bar.volume)
                    for symbol, bars in payload.bars.items()
                    for bar in bars
                    if first_date <= (bar_date := bar.timestamp.date()) <= last_date
                )
                bar_count += insert_bars(connection, rows)
                rss_gib = peak_rss_gib()
                if rss_gib >= self._rss_limit_gib:
                    raise AlpacaMemoryLimitError(rss_gib, self._rss_limit_gib)
                page_token = payload.next_page_token
                if page_token is None:
                    break
                if page_token in seen_tokens:
                    raise AlpacaApiError(status_code=500, message="반복된 daily cache page token")
                seen_tokens.add(page_token)
            complete_batch(connection, index, symbols, request_count, bar_count)
        except BaseException:
            connection.rollback()
            raise
        return request_count

    def _cache_path(self, start_date: dt.date, end_date: dt.date, symbols: tuple[str, ...]) -> Path:
        fingerprint = (
            f"{start_date}\n{end_date}\n{self._batch_size}\n{self._lookback_calendar_days}\n{'\n'.join(symbols)}"
        )
        cache_id = hashlib.sha256(fingerprint.encode()).hexdigest()[:16]
        return self._output_dir / f"daily_range_{start_date}_{end_date}_{cache_id}.sqlite3"

from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from trading_agent.alpaca_bars import AlpacaBarsClient, AlpacaDailyPageRequest
from trading_agent.alpaca_http import AlpacaApiError, AlpacaMemoryLimitError, peak_rss_gib

REFERENCE_HEADER = ("symbol", "prior_session", "prior_close", "average_volume", "history_sessions")


@dataclass(frozen=True, slots=True)
class AlpacaDailyReference:
    symbol: str
    prior_session: dt.date | None
    prior_close: float | None
    average_volume: float | None
    history_sessions: int


@dataclass(frozen=True, slots=True)
class AlpacaReferenceResult:
    archive_dir: Path
    references: tuple[AlpacaDailyReference, ...]
    batch_count: int
    skipped_batch_count: int
    request_count: int
    new_request_count: int


class AlpacaDailyReferenceArchive:
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
        self._bars_client = bars_client
        self._output_dir = output_dir
        self._batch_size = batch_size
        self._lookback_calendar_days = lookback_calendar_days
        self._reference_sessions = reference_sessions
        self._minimum_reference_sessions = minimum_reference_sessions
        self._rss_limit_gib = rss_limit_gib

    def archive_session(self, session_date: dt.date, symbols: tuple[str, ...]) -> AlpacaReferenceResult:
        normalized = tuple(sorted(set(symbols)))
        archive_id = self._archive_id(normalized)
        archive_dir = self._session_dir(session_date, archive_id)
        batches = tuple(
            normalized[offset : offset + self._batch_size] for offset in range(0, len(normalized), self._batch_size)
        )
        skipped = 0
        requests = 0
        new_requests = 0
        for index, batch in enumerate(batches):
            checkpoint_requests = self._completed_request_count(archive_dir, index, batch)
            if checkpoint_requests is not None:
                skipped += 1
                requests += checkpoint_requests
                continue
            batch_requests = self._archive_batch(archive_dir, session_date, index, batch)
            requests += batch_requests
            new_requests += batch_requests
        references = self._load_references(archive_dir)
        self._write_session_metadata(
            archive_dir,
            session_date,
            len(batches),
            skipped,
            requests,
            new_requests,
            len(references),
        )
        return AlpacaReferenceResult(
            archive_dir=archive_dir,
            references=references,
            batch_count=len(batches),
            skipped_batch_count=skipped,
            request_count=requests,
            new_request_count=new_requests,
        )

    def _archive_batch(
        self,
        archive_dir: Path,
        session_date: dt.date,
        index: int,
        symbols: tuple[str, ...],
    ) -> int:
        histories: dict[str, list[tuple[dt.date, float, int]]] = {symbol: [] for symbol in symbols}
        page_token: str | None = None
        seen_tokens: set[str] = set()
        request_count = 0
        while True:
            payload = self._bars_client.fetch_daily_page(
                AlpacaDailyPageRequest(
                    session_date=session_date,
                    symbols=symbols,
                    start_date=session_date - dt.timedelta(days=self._lookback_calendar_days),
                    end_date=session_date - dt.timedelta(days=1),
                    page_token=page_token,
                )
            )
            request_count += 1
            if peak_rss_gib() >= self._rss_limit_gib:
                raise AlpacaMemoryLimitError(peak_rss_gib(), self._rss_limit_gib)
            for symbol, bars in payload.bars.items():
                history = histories.get(symbol)
                if history is None:
                    continue
                for bar in bars:
                    bar_date = bar.timestamp.date()
                    if bar_date < session_date:
                        history.append((bar_date, bar.close, bar.volume))
            page_token = payload.next_page_token
            if page_token is None:
                break
            if page_token in seen_tokens:
                raise AlpacaApiError(status_code=500, message="반복된 daily page token")
            seen_tokens.add(page_token)
        self._write_batch(archive_dir, index, symbols, histories, request_count)
        return request_count

    def _write_batch(
        self,
        archive_dir: Path,
        index: int,
        symbols: tuple[str, ...],
        histories: dict[str, list[tuple[dt.date, float, int]]],
        request_count: int,
    ) -> None:
        archive_dir.mkdir(parents=True, exist_ok=True)
        data_path = archive_dir / f"batch_{index:05d}.csv.gz"
        temporary = data_path.with_suffix(".gz.part")
        with gzip.open(temporary, "wt", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(REFERENCE_HEADER)
            for symbol in symbols:
                history = sorted(histories[symbol])[-self._reference_sessions :]
                available = len(history) >= self._minimum_reference_sessions
                writer.writerow(
                    (
                        symbol,
                        history[-1][0].isoformat() if available else "",
                        history[-1][1] if available else "",
                        sum(item[2] for item in history) / len(history) if available else "",
                        len(history),
                    )
                )
        temporary.replace(data_path)
        metadata = {
            "status": "complete",
            "symbols": symbols,
            "request_count": request_count,
            "reference_sessions": self._reference_sessions,
            "minimum_reference_sessions": self._minimum_reference_sessions,
        }
        self._write_json(archive_dir / f"batch_{index:05d}.metadata.json", metadata)

    def _completed_request_count(
        self,
        archive_dir: Path,
        index: int,
        symbols: tuple[str, ...],
    ) -> int | None:
        data_path = archive_dir / f"batch_{index:05d}.csv.gz"
        metadata_path = archive_dir / f"batch_{index:05d}.metadata.json"
        if not data_path.is_file() or not metadata_path.is_file():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        complete = (
            metadata.get("status") == "complete"
            and tuple(metadata.get("symbols", ())) == symbols
            and metadata.get("reference_sessions") == self._reference_sessions
            and metadata.get("minimum_reference_sessions") == self._minimum_reference_sessions
        )
        request_count = metadata.get("request_count")
        return request_count if complete and isinstance(request_count, int) else None

    def _load_references(self, archive_dir: Path) -> tuple[AlpacaDailyReference, ...]:
        references: list[AlpacaDailyReference] = []
        for path in sorted(archive_dir.glob("batch_*.csv.gz")):
            with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    prior_session = row["prior_session"]
                    references.append(
                        AlpacaDailyReference(
                            symbol=row["symbol"],
                            prior_session=dt.date.fromisoformat(prior_session) if prior_session else None,
                            prior_close=float(row["prior_close"]) if row["prior_close"] else None,
                            average_volume=float(row["average_volume"]) if row["average_volume"] else None,
                            history_sessions=int(row["history_sessions"]),
                        )
                    )
        return tuple(references)

    def _archive_id(self, symbols: tuple[str, ...]) -> str:
        fingerprint = (
            f"{self._batch_size}\n{self._lookback_calendar_days}\n{self._reference_sessions}\n"
            f"{self._minimum_reference_sessions}\n{'\n'.join(symbols)}"
        )
        return hashlib.sha256(fingerprint.encode()).hexdigest()[:16]

    def _session_dir(self, session_date: dt.date, archive_id: str) -> Path:
        return self._output_dir / session_date.strftime("%Y/%m/%d") / f"reference_{archive_id}"

    def _write_session_metadata(
        self,
        archive_dir: Path,
        session_date: dt.date,
        batch_count: int,
        skipped: int,
        requests: int,
        new_requests: int,
        reference_count: int,
    ) -> None:
        self._write_json(
            archive_dir / "session.metadata.json",
            {
                "status": "complete",
                "session_date": session_date.isoformat(),
                "batch_count": batch_count,
                "skipped_batch_count": skipped,
                "request_count": requests,
                "new_request_count": new_requests,
                "reference_count": reference_count,
            },
        )

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(path)

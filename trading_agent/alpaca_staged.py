from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx2

from trading_agent.alpaca_archive import AlpacaMinuteArchive
from trading_agent.alpaca_bars import AlpacaBarsClient
from trading_agent.alpaca_daily_cache import AlpacaDailyRangeCache
from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_models import AlpacaBarWindow
from trading_agent.alpaca_reference import AlpacaDailyReference, AlpacaDailyReferenceArchive
from trading_agent.alpaca_scanner import (
    AlpacaScannerConfig,
    scan_alpaca_archive,
    write_scanner_decisions,
)
from trading_agent.alpaca_scanner_quality_models import (
    PORTFOLIO_LIMIT,
    scanner_quality_grid,
    select_scanner_grid_union,
)


class AlpacaStagedConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AlpacaStagedConfig:
    scanner_cutoff: dt.time = dt.time(9, 30)
    scanner: AlpacaScannerConfig = field(default_factory=AlpacaScannerConfig)
    batch_size: int = 100
    request_interval_seconds: float = 0.35
    reference_lookback_calendar_days: int = 45
    reference_sessions: int = 20
    minimum_reference_sessions: int = 10
    rss_limit_gib: float = 10.0

    def __post_init__(self) -> None:
        if self.scanner_cutoff <= dt.time(4) or self.scanner_cutoff >= dt.time(20):
            raise AlpacaStagedConfigError("스캐너 마감은 04:00보다 늦고 20:00보다 빨라야 합니다")
        if self.batch_size <= 0 or self.request_interval_seconds < 0 or self.rss_limit_gib <= 0:
            raise AlpacaStagedConfigError("수집 배치·요청 간격·RSS 제한이 올바르지 않습니다")
        if not 0 < self.minimum_reference_sessions <= self.reference_sessions:
            raise AlpacaStagedConfigError("일봉 최소 이력은 참조 세션 수 이하여야 합니다")
        if self.reference_lookback_calendar_days < self.reference_sessions:
            raise AlpacaStagedConfigError("일봉 달력 조회기간은 참조 세션 수 이상이어야 합니다")


@dataclass(frozen=True, slots=True)
class AlpacaStagedResult:
    session_date: dt.date
    selected_symbols: tuple[str, ...]
    base_selected_symbols: tuple[str, ...]
    decisions_path: Path
    scanner_bar_count: int
    candidate_bar_count: int
    request_count: int
    new_request_count: int
    skipped_batch_count: int


@dataclass(frozen=True, slots=True)
class _ReferenceData:
    references: tuple[AlpacaDailyReference, ...]
    request_count: int
    new_request_count: int
    skipped_batch_count: int
    source: str


class AlpacaStagedArchive:
    def __init__(
        self,
        client: httpx2.Client,
        credentials: AlpacaCredentials,
        output_dir: Path,
        config: AlpacaStagedConfig,
        daily_cache: AlpacaDailyRangeCache | None = None,
    ) -> None:
        self._output_dir = output_dir
        self._config = config
        self._daily_cache = daily_cache
        bars_client = AlpacaBarsClient(
            client=client,
            credentials=credentials,
            request_interval_seconds=config.request_interval_seconds,
        )
        self._references = AlpacaDailyReferenceArchive(
            bars_client=bars_client,
            output_dir=output_dir / "daily_reference",
            batch_size=config.batch_size,
            lookback_calendar_days=config.reference_lookback_calendar_days,
            reference_sessions=config.reference_sessions,
            minimum_reference_sessions=config.minimum_reference_sessions,
            rss_limit_gib=config.rss_limit_gib,
        )
        minute_options = {
            "client": client,
            "credentials": credentials,
            "batch_size": config.batch_size,
            "rss_limit_gib": config.rss_limit_gib,
            "request_interval_seconds": config.request_interval_seconds,
        }
        self._scanner_minutes = AlpacaMinuteArchive(output_dir=output_dir / "scanner_minutes", **minute_options)
        self._candidate_minutes = AlpacaMinuteArchive(output_dir=output_dir / "candidate_minutes", **minute_options)

    def archive_session(self, session_date: dt.date, symbols: tuple[str, ...]) -> AlpacaStagedResult:
        references = self._reference_data(session_date, symbols)
        scanner_window = AlpacaBarWindow(start=dt.time(4), end=self._config.scanner_cutoff)
        scanner_result = self._scanner_minutes.archive_session(session_date, symbols, window=scanner_window)
        decisions = scan_alpaca_archive(
            archive_dir=scanner_result.archive_dir,
            session_date=session_date,
            cutoff=self._config.scanner_cutoff,
            symbols=symbols,
            references=references.references,
            config=self._config.scanner,
        )
        base_selected = tuple(
            decision.symbol
            for decision in sorted(decisions, key=lambda item: item.rank or self._config.scanner.max_candidates + 1)
            if decision.selected
        )
        selected = tuple(sorted(set(base_selected) | set(select_scanner_grid_union(decisions))))
        session_id = self._session_id(symbols)
        decisions_path = (
            self._output_dir
            / "scanner_decisions"
            / session_date.strftime("%Y/%m/%d")
            / f"scanner_decisions_{session_id}.csv.gz"
        )
        write_scanner_decisions(decisions_path, decisions)
        candidate_window = AlpacaBarWindow(start=self._config.scanner_cutoff, end=dt.time(20))
        candidate_result = self._candidate_minutes.archive_session(
            session_date,
            selected,
            window=candidate_window,
        )
        request_count = references.request_count + scanner_result.request_count + candidate_result.request_count
        new_request_count = (
            references.new_request_count + scanner_result.new_request_count + candidate_result.new_request_count
        )
        skipped = (
            references.skipped_batch_count + scanner_result.skipped_batch_count + candidate_result.skipped_batch_count
        )
        result = AlpacaStagedResult(
            session_date=session_date,
            selected_symbols=selected,
            base_selected_symbols=base_selected,
            decisions_path=decisions_path,
            scanner_bar_count=scanner_result.bar_count,
            candidate_bar_count=candidate_result.bar_count,
            request_count=request_count,
            new_request_count=new_request_count,
            skipped_batch_count=skipped,
        )
        self._write_metadata(result, len(set(symbols)), session_id, references.source)
        return result

    def _reference_data(self, session_date: dt.date, symbols: tuple[str, ...]) -> _ReferenceData:
        if self._daily_cache is not None:
            return _ReferenceData(
                references=self._daily_cache.references_for_session(session_date, symbols),
                request_count=0,
                new_request_count=0,
                skipped_batch_count=0,
                source="range_cache",
            )
        result = self._references.archive_session(session_date, symbols)
        return _ReferenceData(
            references=result.references,
            request_count=result.request_count,
            new_request_count=result.new_request_count,
            skipped_batch_count=result.skipped_batch_count,
            source="per_day_api",
        )

    def _write_metadata(
        self,
        result: AlpacaStagedResult,
        symbol_count: int,
        session_id: str,
        reference_source: str,
    ) -> None:
        path = (
            self._output_dir
            / "staged_sessions"
            / result.session_date.strftime("%Y/%m/%d")
            / f"session_{session_id}.metadata.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "status": "complete",
                    "session_date": result.session_date.isoformat(),
                    "scanner_cutoff": self._config.scanner_cutoff.isoformat(),
                    "scanner_config": asdict(self._config.scanner),
                    "reference_sessions": self._config.reference_sessions,
                    "minimum_reference_sessions": self._config.minimum_reference_sessions,
                    "reference_lookback_calendar_days": self._config.reference_lookback_calendar_days,
                    "reference_source": reference_source,
                    "universe_symbol_count": symbol_count,
                    "selected_symbol_count": len(result.selected_symbols),
                    "selected_symbols": result.selected_symbols,
                    "base_selected_symbol_count": len(result.base_selected_symbols),
                    "base_selected_symbols": result.base_selected_symbols,
                    "candidate_selection_contract": "base_plus_scanner_grid_top_10_union",
                    "scanner_grid_config_count": len(scanner_quality_grid()),
                    "scanner_grid_portfolio_limit": PORTFOLIO_LIMIT,
                    "scanner_bar_count": result.scanner_bar_count,
                    "candidate_bar_count": result.candidate_bar_count,
                    "request_count": result.request_count,
                    "new_request_count": result.new_request_count,
                    "skipped_batch_count": result.skipped_batch_count,
                    "selection_uses_bars_strictly_before_cutoff": True,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _session_id(self, symbols: tuple[str, ...]) -> str:
        payload = {
            "scanner_cutoff": self._config.scanner_cutoff.isoformat(),
            "scanner": asdict(self._config.scanner),
            "reference_sessions": self._config.reference_sessions,
            "minimum_reference_sessions": self._config.minimum_reference_sessions,
            "reference_lookback_calendar_days": self._config.reference_lookback_calendar_days,
            "reference_source": "range_cache" if self._daily_cache is not None else "per_day_api",
            "symbols": sorted(set(symbols)),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()[:16]

#!/usr/bin/env -S uv run --python 3.12 python

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated, Final

import typer
from rich import print as rprint

from trading_agent.alpaca_bars import AlpacaBarsClient
from trading_agent.alpaca_daily_cache import AlpacaDailyRangeCache
from trading_agent.alpaca_http import (
    DEFAULT_ALPACA_SECRET_PATH,
    AlpacaApiError,
    AlpacaCredentials,
    AlpacaMemoryLimitError,
    AlpacaSecretFileError,
    MissingAlpacaCredentialsError,
    create_alpaca_client,
    load_alpaca_credentials,
    peak_rss_gib,
)
from trading_agent.alpaca_scanner import AlpacaScannerConfig
from trading_agent.alpaca_staged import AlpacaStagedArchive, AlpacaStagedConfig
from trading_agent.alpaca_universe import (
    ALPACA_TRADING_URL,
    fetch_alpaca_universe,
    write_universe_snapshot,
)
from trading_agent.us_equity_calendar import regular_session_bounds

DEFAULT_OUTPUT_DIR: Final = (
    Path(__file__).resolve().parents[2]
    / "outputs/trading_strategy_research_hub/02_momentum_strategies"
    / "00_intraday_data_feasibility/alpaca_staged_archive"
)


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise typer.BadParameter(f"날짜는 YYYY-MM-DD 형식이어야 합니다: {value}") from error


def parse_time(value: str) -> dt.time:
    try:
        return dt.time.fromisoformat(value)
    except ValueError as error:
        raise typer.BadParameter(f"시각은 HH:MM 형식이어야 합니다: {value}") from error


def session_dates(start: dt.date, end: dt.date) -> tuple[dt.date, ...]:
    if end < start:
        raise typer.BadParameter("종료일은 시작일보다 빠를 수 없습니다")
    return tuple(
        day
        for offset in range((end - start).days + 1)
        if regular_session_bounds(day := start + dt.timedelta(days=offset)) is not None
    )


def load_symbols_file(path: Path) -> tuple[str, ...]:
    symbols = tuple(
        sorted({line.strip().upper() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()})
    )
    if not symbols:
        raise typer.BadParameter("종목 파일이 비어 있습니다")
    return symbols


def load_symbols(
    symbols_file: Path | None,
    output_dir: Path,
    credentials: AlpacaCredentials,
) -> tuple[tuple[str, ...], Path]:
    if symbols_file is not None:
        return load_symbols_file(symbols_file), symbols_file
    with create_alpaca_client(ALPACA_TRADING_URL) as universe_client:
        assets = fetch_alpaca_universe(universe_client, credentials)
    snapshot_path = output_dir / f"universe_snapshot_{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}.csv"
    write_universe_snapshot(snapshot_path, assets)
    return tuple(asset.symbol for asset in assets), snapshot_path


def main(
    start: Annotated[str, typer.Option("--start", help="첫 조회일 YYYY-MM-DD")],
    end: Annotated[str, typer.Option("--end", help="마지막 조회일 YYYY-MM-DD")],
    output_dir: Annotated[Path, typer.Option("--output-dir")] = DEFAULT_OUTPUT_DIR,
    secret_path: Annotated[Path, typer.Option("--secret-path")] = DEFAULT_ALPACA_SECRET_PATH,
    symbols_file: Annotated[Path | None, typer.Option("--symbols-file")] = None,
    scanner_cutoff: Annotated[str, typer.Option("--scanner-cutoff", help="뉴욕 현지시각 HH:MM")] = "09:30",
    batch_size: Annotated[int, typer.Option("--batch-size", min=1, max=200)] = 100,
    max_candidates: Annotated[int, typer.Option("--max-candidates", min=1, max=1000)] = 200,
    min_change_pct: Annotated[float, typer.Option("--min-change-pct")] = 0.02,
    min_price: Annotated[float, typer.Option("--min-price", min=0.01)] = 0.50,
    max_price: Annotated[float, typer.Option("--max-price", min=0.01)] = 100.0,
    min_dollar_volume: Annotated[float, typer.Option("--min-dollar-volume", min=0.0)] = 250_000.0,
    min_adv_fraction: Annotated[float, typer.Option("--min-adv-fraction", min=0.0)] = 0.01,
    range_daily_cache: Annotated[
        bool,
        typer.Option(
            "--range-daily-cache/--per-day-daily-reference",
            help="전체 조회범위 일봉을 한 번 저장하거나 날짜마다 다시 조회",
        ),
    ] = True,
) -> None:
    dates = session_dates(parse_date(start), parse_date(end))
    if not dates:
        raise typer.BadParameter("조회 범위에 평일이 없습니다")
    try:
        credentials = load_alpaca_credentials(secret_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        symbols, snapshot = load_symbols(symbols_file, output_dir, credentials)
        scanner = AlpacaScannerConfig(
            min_change_pct=min_change_pct,
            min_price=min_price,
            max_price=max_price,
            min_dollar_volume=min_dollar_volume,
            min_adv_fraction=min_adv_fraction,
            max_candidates=max_candidates,
        )
        config = AlpacaStagedConfig(
            scanner_cutoff=parse_time(scanner_cutoff),
            scanner=scanner,
            batch_size=batch_size,
        )
        rprint(
            f"[cyan]Alpaca 2단계 수집 시작[/cyan] 유니버스 {len(symbols):,}개, "
            f"평일 {len(dates):,}일, 스캔 마감 {config.scanner_cutoff}, 원천 {snapshot}"
        )
        with create_alpaca_client() as data_client:
            daily_cache = None
            if range_daily_cache:
                daily_cache = AlpacaDailyRangeCache(
                    bars_client=AlpacaBarsClient(
                        data_client,
                        credentials,
                        config.request_interval_seconds,
                    ),
                    output_dir=output_dir / "daily_range_cache",
                    batch_size=config.batch_size,
                    lookback_calendar_days=config.reference_lookback_calendar_days,
                    reference_sessions=config.reference_sessions,
                    minimum_reference_sessions=config.minimum_reference_sessions,
                    rss_limit_gib=config.rss_limit_gib,
                )
                cache_result = daily_cache.build(dates[0], dates[-1], symbols)
                rprint(
                    f"[blue]일봉 범위 캐시[/blue] 행 {cache_result.bar_count:,}, "
                    f"신규 요청 {cache_result.new_request_count:,}/원천 {cache_result.request_count:,}, "
                    f"재사용 {cache_result.skipped_batch_count:,}/{cache_result.batch_count:,}, "
                    f"{cache_result.database_path}"
                )
            archive = AlpacaStagedArchive(
                data_client,
                credentials,
                output_dir,
                config,
                daily_cache=daily_cache,
            )
            for session_date in dates:
                result = archive.archive_session(session_date, symbols)
                rprint(
                    f"[green]{session_date}[/green] 스캐너봉 {result.scanner_bar_count:,}, "
                    f"후보 {len(result.selected_symbols):,}, 후보봉 {result.candidate_bar_count:,}, "
                    f"신규 요청 {result.new_request_count:,}/원천 {result.request_count:,}, "
                    f"재사용 배치 {result.skipped_batch_count:,}, "
                    f"RSS {peak_rss_gib():.2f}GiB"
                )
    except (
        FileNotFoundError,
        AlpacaSecretFileError,
        MissingAlpacaCredentialsError,
        AlpacaApiError,
        AlpacaMemoryLimitError,
        ValueError,
    ) as error:
        raise typer.BadParameter(str(error)) from None


if __name__ == "__main__":
    typer.run(main)

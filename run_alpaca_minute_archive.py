#!/usr/bin/env -S uv run --python 3.12 python

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated, Final

import typer
from rich import print as rprint

from trading_agent.alpaca_archive import (
    AlpacaMinuteArchive,
)
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
from trading_agent.alpaca_universe import (
    ALPACA_TRADING_URL,
    fetch_alpaca_universe,
    write_universe_snapshot,
)
from trading_agent.us_equity_calendar import regular_session_bounds

DEFAULT_OUTPUT_DIR: Final = (
    Path(__file__).resolve().parents[2]
    / "outputs/trading_strategy_research_hub/02_momentum_strategies"
    / "00_intraday_data_feasibility/alpaca_archive"
)


def session_dates(start: dt.date, end: dt.date) -> tuple[dt.date, ...]:
    if end < start:
        raise typer.BadParameter("종료일은 시작일보다 빠를 수 없습니다")
    day_count = (end - start).days + 1
    return tuple(
        session_date
        for offset in range(day_count)
        if regular_session_bounds(session_date := start + dt.timedelta(days=offset)) is not None
    )


def load_symbols_file(path: Path) -> tuple[str, ...]:
    symbols = {
        raw_line.strip().upper() for raw_line in path.read_text(encoding="utf-8").splitlines() if raw_line.strip() != ""
    }
    if not symbols:
        raise typer.BadParameter("종목 파일이 비어 있습니다")
    return tuple(sorted(symbols))


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise typer.BadParameter(f"날짜는 YYYY-MM-DD 형식이어야 합니다: {value}") from error


def load_cli_credentials(path: Path) -> AlpacaCredentials:
    try:
        return load_alpaca_credentials(path)
    except FileNotFoundError as error:
        raise typer.BadParameter(f"Alpaca 키 파일을 찾을 수 없습니다: {path}") from error
    except (AlpacaSecretFileError, MissingAlpacaCredentialsError) as error:
        raise typer.BadParameter(str(error)) from error


def main(
    start: Annotated[str, typer.Option("--start", help="첫 조회일 YYYY-MM-DD")],
    end: Annotated[str, typer.Option("--end", help="마지막 조회일 YYYY-MM-DD")],
    output_dir: Annotated[Path, typer.Option("--output-dir")] = DEFAULT_OUTPUT_DIR,
    secret_path: Annotated[Path, typer.Option("--secret-path")] = DEFAULT_ALPACA_SECRET_PATH,
    symbols_file: Annotated[Path | None, typer.Option("--symbols-file")] = None,
    batch_size: Annotated[int, typer.Option("--batch-size", min=1, max=200)] = 100,
) -> None:
    dates = session_dates(parse_date(start), parse_date(end))
    if not dates:
        raise typer.BadParameter("조회 범위에 평일이 없습니다")
    credentials = load_cli_credentials(secret_path)
    if symbols_file is None:
        try:
            with create_alpaca_client(ALPACA_TRADING_URL) as universe_client:
                assets = fetch_alpaca_universe(universe_client, credentials)
        except AlpacaApiError as error:
            raise typer.BadParameter(str(error)) from None
        snapshot_path = output_dir / f"universe_snapshot_{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}.csv"
        write_universe_snapshot(snapshot_path, assets)
        symbols = tuple(asset.symbol for asset in assets)
    else:
        symbols = load_symbols_file(symbols_file)
        snapshot_path = symbols_file
    output_dir.mkdir(parents=True, exist_ok=True)
    rprint(
        f"[cyan]Alpaca SIP 1분봉 수집 시작[/cyan] 종목 {len(symbols):,}개, "
        f"평일 {len(dates):,}일, 유니버스 {snapshot_path}"
    )
    try:
        with create_alpaca_client() as data_client:
            archive = AlpacaMinuteArchive(
                client=data_client,
                credentials=credentials,
                output_dir=output_dir,
                batch_size=batch_size,
            )
            for session_date in dates:
                result = archive.archive_session(session_date, symbols)
                rprint(
                    f"[green]{session_date}[/green] 분봉 {result.bar_count:,}개, "
                    f"요청 {result.request_count:,}회, 재사용 {result.skipped_batch_count:,}/"
                    f"{result.batch_count:,}, RSS {peak_rss_gib():.2f}GiB"
                )
    except (AlpacaApiError, AlpacaMemoryLimitError) as error:
        raise typer.BadParameter(str(error)) from None


if __name__ == "__main__":
    typer.run(main)

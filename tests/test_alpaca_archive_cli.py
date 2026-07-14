from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import typer

from run_alpaca_minute_archive import load_cli_credentials, load_symbols_file, session_dates
from run_alpaca_staged_archive import session_dates as staged_session_dates


def test_session_dates_includes_weekdays_and_excludes_weekends() -> None:
    dates = session_dates(dt.date(2026, 7, 10), dt.date(2026, 7, 13))

    assert dates == (dt.date(2026, 7, 10), dt.date(2026, 7, 13))


def test_session_dates_excludes_published_market_holidays_in_both_collectors() -> None:
    # Given
    start = dt.date(2026, 7, 3)
    end = dt.date(2026, 7, 6)

    # When
    minute_dates = session_dates(start, end)
    staged_dates = staged_session_dates(start, end)

    # Then
    assert minute_dates == (dt.date(2026, 7, 6),)
    assert staged_dates == minute_dates


def test_session_dates_excludes_2025_carter_national_day_of_mourning() -> None:
    # Given
    start = dt.date(2025, 1, 8)
    end = dt.date(2025, 1, 10)

    # When
    dates = staged_session_dates(start, end)

    # Then
    assert dates == (dt.date(2025, 1, 8), dt.date(2025, 1, 10))


def test_load_symbols_file_normalizes_and_deduplicates_symbols(tmp_path: Path) -> None:
    path = tmp_path / "symbols.txt"
    path.write_text("aapl\nMSFT\nAAPL\n\n", encoding="utf-8")

    symbols = load_symbols_file(path)

    assert symbols == ("AAPL", "MSFT")


def test_load_cli_credentials_reports_missing_file_without_raw_os_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.env"

    with pytest.raises(typer.BadParameter, match="Alpaca 키 파일을 찾을 수 없습니다"):
        _ = load_cli_credentials(missing)

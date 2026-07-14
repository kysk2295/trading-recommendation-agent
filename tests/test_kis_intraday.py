from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scr_backtest.kis_intraday import (
    KisApiError,
    KisMinutePayload,
    MissingKisCredentialsError,
    next_minute_cursor,
    parse_minute_payload,
    require_kis_credentials,
    write_minute_csv,
)


def test_require_kis_credentials_rejects_missing_environment() -> None:
    given_environment: dict[str, str] = {}

    with pytest.raises(MissingKisCredentialsError, match="KIS_APP_KEY"):
        _ = require_kis_credentials(given_environment)


def test_require_kis_credentials_redacts_secret_from_representation() -> None:
    given_secret = "top-secret-value"

    when_credentials = require_kis_credentials({"KIS_APP_KEY": "test-key", "KIS_APP_SECRET": given_secret})

    assert given_secret not in repr(when_credentials)


def test_parse_minute_payload_orders_and_types_exchange_timestamps() -> None:
    given_payload: KisMinutePayload = {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리 되었습니다.",
        "output2": [
            {
                "xymd": "20260710",
                "xhms": "175100",
                "kymd": "20260711",
                "khms": "065100",
                "open": "315.1000",
                "high": "315.2000",
                "low": "315.0000",
                "last": "315.1500",
                "evol": "12",
                "eamt": "3782",
            },
            {
                "xymd": "20260710",
                "xhms": "175000",
                "kymd": "20260711",
                "khms": "065000",
                "open": "315.0200",
                "high": "315.1800",
                "low": "315.0200",
                "last": "315.1800",
                "evol": "24",
                "eamt": "7564",
            },
        ],
    }

    when_bars = parse_minute_payload(given_payload)

    assert when_bars[0].exchange_timestamp == dt.datetime(2026, 7, 10, 17, 50, tzinfo=ZoneInfo("America/New_York"))
    assert when_bars[0].korea_timestamp == dt.datetime(2026, 7, 11, 6, 50, tzinfo=ZoneInfo("Asia/Seoul"))
    assert when_bars[0].close == 315.18
    assert when_bars[0].volume == 24


def test_next_minute_cursor_moves_before_earliest_bar() -> None:
    given_bars = parse_minute_payload(
        {
            "rt_cd": "0",
            "msg_cd": "MCA00000",
            "msg1": "정상처리 되었습니다.",
            "output2": [
                {
                    "xymd": "20260710",
                    "xhms": "175000",
                    "kymd": "20260711",
                    "khms": "065000",
                    "open": "315.02",
                    "high": "315.18",
                    "low": "315.02",
                    "last": "315.18",
                    "evol": "24",
                    "eamt": "7564",
                }
            ],
        }
    )

    when_cursor = next_minute_cursor(given_bars, interval_minutes=1)

    assert when_cursor == "20260710174900"


def test_parse_minute_payload_raises_typed_api_error() -> None:
    given_payload: KisMinutePayload = {
        "rt_cd": "1",
        "msg_cd": "EGW00201",
        "msg1": "초당 거래건수를 초과하였습니다.",
        "output2": [],
    }

    with pytest.raises(KisApiError, match="EGW00201"):
        _ = parse_minute_payload(given_payload)


def test_write_minute_csv_persists_typed_bars_without_credentials(
    tmp_path: Path,
) -> None:
    given_bars = parse_minute_payload(
        {
            "rt_cd": "0",
            "msg_cd": "MCA00000",
            "msg1": "정상처리 되었습니다.",
            "output2": [
                {
                    "xymd": "20260710",
                    "xhms": "175000",
                    "kymd": "20260711",
                    "khms": "065000",
                    "open": "315.02",
                    "high": "315.18",
                    "low": "315.02",
                    "last": "315.18",
                    "evol": "24",
                    "eamt": "7564",
                }
            ],
        }
    )
    given_path = tmp_path / "AAPL.csv"

    write_minute_csv(given_path, given_bars)

    with given_path.open(encoding="utf-8", newline="") as handle:
        when_rows = tuple(csv.DictReader(handle))
    assert when_rows[0]["exchange_timestamp"] == "2026-07-10T17:50:00-04:00"
    assert when_rows[0]["close"] == "315.18"
    assert "secret" not in when_rows[0]

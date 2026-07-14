from __future__ import annotations

import datetime as dt
import gzip
import json
from pathlib import Path

import httpx2
import pytest

from trading_agent.alpaca_archive import (
    AlpacaApiError,
    AlpacaCredentials,
    AlpacaMinuteArchive,
)
from trading_agent.alpaca_http import AlpacaSecretFileError, load_alpaca_credentials
from trading_agent.alpaca_models import AlpacaBarWindow


def test_load_alpaca_credentials_rejects_world_readable_secret(tmp_path: Path) -> None:
    secret = tmp_path / "alpaca.env"
    secret.write_text(
        "APCA_API_KEY_ID=test-key\nAPCA_API_SECRET_KEY=test-secret\n",
        encoding="utf-8",
    )
    secret.chmod(0o644)

    with pytest.raises(AlpacaSecretFileError, match="600"):
        _ = load_alpaca_credentials(secret)


def test_archive_session_writes_sip_minute_bars_and_metadata(tmp_path: Path) -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "bars": {
                    "AAPL": [
                        {
                            "t": "2026-06-12T13:30:00Z",
                            "o": 198.0,
                            "h": 199.0,
                            "l": 197.5,
                            "c": 198.5,
                            "v": 12345,
                            "n": 321,
                            "vw": 198.4,
                        }
                    ]
                },
                "next_page_token": None,
            },
        )

    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as client:
        archive = AlpacaMinuteArchive(
            client=client,
            credentials=AlpacaCredentials("test-key", "test-secret"),
            output_dir=tmp_path,
            batch_size=100,
        )
        result = archive.archive_session(dt.date(2026, 6, 12), ("AAPL",))

    assert result.bar_count == 1
    assert result.batch_count == 1
    assert requests[0].url.params["feed"] == "sip"
    assert requests[0].url.params["timeframe"] == "1Min"
    assert requests[0].url.params["adjustment"] == "raw"
    assert requests[0].url.params["asof"] == "2026-06-12"
    assert requests[0].headers["APCA-API-KEY-ID"] == "test-key"
    archive_dir = next((tmp_path / "2026/06/12").glob("archive_*"))
    rows = (archive_dir / "batch_00000.csv.gz").read_bytes()
    assert rows[:2] == b"\x1f\x8b"
    with gzip.open(archive_dir / "batch_00000.csv.gz", "rt", encoding="utf-8") as gzip_handle:
        archived_csv = gzip_handle.read()
    assert "AAPL,2026-06-12T13:30:00+00:00" in archived_csv
    metadata = json.loads((archive_dir / "batch_00000.metadata.json").read_text(encoding="utf-8"))
    assert metadata["bar_count"] == 1
    assert metadata["feed"] == "sip"


def test_archive_session_uses_window_and_separates_its_checkpoint(tmp_path: Path) -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"bars": {}, "next_page_token": None})

    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as client:
        archive = AlpacaMinuteArchive(
            client=client,
            credentials=AlpacaCredentials("test-key", "test-secret"),
            output_dir=tmp_path,
            batch_size=100,
            request_interval_seconds=0.0,
        )
        _ = archive.archive_session(dt.date(2026, 6, 12), ("AAPL",))
        _ = archive.archive_session(
            dt.date(2026, 6, 12),
            ("AAPL",),
            window=AlpacaBarWindow(start=dt.time(4), end=dt.time(9, 35)),
        )

    assert requests[1].url.params["start"] == "2026-06-12T08:00:00+00:00"
    assert requests[1].url.params["end"] == "2026-06-12T13:35:00+00:00"
    archives = tuple((tmp_path / "2026/06/12").glob("archive_*"))
    assert len(archives) == 2
    metadata = tuple(json.loads((path / "session.metadata.json").read_text(encoding="utf-8")) for path in archives)
    assert {item["window_start"] for item in metadata} == {"04:00:00"}
    assert {item["window_end"] for item in metadata} == {"09:35:00", "20:00:00"}


def test_archive_session_reuses_completed_batch_without_http(tmp_path: Path) -> None:
    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json={"bars": {}, "next_page_token": None})),
    ) as client:
        archive = AlpacaMinuteArchive(
            client=client,
            credentials=AlpacaCredentials("test-key", "test-secret"),
            output_dir=tmp_path,
            batch_size=100,
            request_interval_seconds=0.0,
        )
        _ = archive.archive_session(dt.date(2026, 6, 12), ("AAPL",))

    def reject_http(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(request.url)

    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(reject_http),
    ) as client:
        archive = AlpacaMinuteArchive(
            client=client,
            credentials=AlpacaCredentials("test-key", "test-secret"),
            output_dir=tmp_path,
            batch_size=100,
        )
        result = archive.archive_session(dt.date(2026, 6, 12), ("AAPL",))

    assert result.bar_count == 0
    assert result.skipped_batch_count == 1


def test_archive_session_redacts_credentials_from_api_failure(tmp_path: Path) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            403,
            request=request,
            json={"message": "forbidden"},
        )

    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as client:
        archive = AlpacaMinuteArchive(
            client=client,
            credentials=AlpacaCredentials("test-key", "test-secret"),
            output_dir=tmp_path,
            batch_size=100,
        )
        with pytest.raises(AlpacaApiError) as captured:
            _ = archive.archive_session(dt.date(2026, 6, 12), ("AAPL",))

    rendered = str(captured.value)
    assert "403" in rendered
    assert "forbidden" in rendered
    assert "test-key" not in rendered
    assert "test-secret" not in rendered


def test_archive_session_does_not_reuse_checkpoint_for_different_symbols(tmp_path: Path) -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"bars": {}, "next_page_token": None})

    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as client:
        archive = AlpacaMinuteArchive(
            client=client,
            credentials=AlpacaCredentials("test-key", "test-secret"),
            output_dir=tmp_path,
            batch_size=100,
            request_interval_seconds=0.0,
        )
        _ = archive.archive_session(dt.date(2026, 6, 12), ("MSFT",))
        result = archive.archive_session(dt.date(2026, 6, 12), ("AAPL",))

    assert result.skipped_batch_count == 0
    assert requests[1].url.params["symbols"] == "AAPL"


def test_archive_session_separates_different_universes(tmp_path: Path) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"bars": {}, "next_page_token": None})

    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as client:
        archive = AlpacaMinuteArchive(
            client=client,
            credentials=AlpacaCredentials("test-key", "test-secret"),
            output_dir=tmp_path,
            batch_size=100,
            request_interval_seconds=0.0,
        )
        _ = archive.archive_session(dt.date(2026, 6, 12), ("AAPL",))
        _ = archive.archive_session(dt.date(2026, 6, 12), ("MSFT",))

    archives = tuple((tmp_path / "2026/06/12").glob("archive_*"))
    assert len(archives) == 2
    assert all((path / "session.metadata.json").is_file() for path in archives)


def test_archive_session_paces_paginated_requests(tmp_path: Path) -> None:
    requests: list[httpx2.Request] = []
    current_time = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        return current_time[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        current_time[0] += seconds

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        page = len(requests)
        return httpx2.Response(
            200,
            json={
                "bars": {},
                "next_page_token": "next" if page == 1 else None,
            },
        )

    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as client:
        archive = AlpacaMinuteArchive(
            client=client,
            credentials=AlpacaCredentials("test-key", "test-secret"),
            output_dir=tmp_path,
            batch_size=100,
            request_interval_seconds=0.31,
            monotonic=clock,
            sleeper=sleep,
        )
        result = archive.archive_session(dt.date(2026, 6, 12), ("AAPL",))

    assert result.request_count == 2
    assert requests[1].url.params["page_token"] == "next"
    assert sleeps == [0.31]


def test_alpaca_api_error_allows_python_traceback_mutation() -> None:
    error = AlpacaApiError(status_code=401, message="Unauthorized")

    error.__traceback__ = None

    assert str(error) == "Alpaca API 오류 401: Unauthorized"

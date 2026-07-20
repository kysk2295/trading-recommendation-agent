from __future__ import annotations

import datetime as dt
import json
import stat
from pathlib import Path

import pytest
import typer

import run_alpaca_news_capability_registry as cli
from trading_agent.alpaca_news_collection import collect_alpaca_news
from trading_agent.alpaca_news_models import AlpacaNewsRawResponse, AlpacaNewsRequest
from trading_agent.alpaca_news_store import AlpacaNewsStore

START = dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC)
END = START + dt.timedelta(hours=1)
RECEIVED = END + dt.timedelta(seconds=1)


class _Fetcher:
    def fetch_page(
        self,
        request: AlpacaNewsRequest,
        page_index: int,
        page_token: str | None,
    ) -> AlpacaNewsRawResponse:
        return AlpacaNewsRawResponse(
            request_id=request.request_id,
            page_index=page_index,
            page_token=page_token,
            received_at=RECEIVED,
            status_code=200,
            content_type="application/json",
            raw_payload=json.dumps(
                {
                    "news": [
                        {
                            "id": 1,
                            "headline": "Private headline",
                            "source": "benzinga",
                            "symbols": ["AAPL"],
                            "created_at": "2026-07-21T13:30:00Z",
                            "updated_at": "2026-07-21T13:31:00Z",
                            "url": "https://example.invalid/private/1",
                        }
                    ],
                    "next_page_token": None,
                }
            ).encode(),
        )


def test_local_projection_appends_once_then_replays_redacted(tmp_path: Path) -> None:
    database = _database(tmp_path)
    registry = tmp_path / "registry" / "capabilities.sqlite3"
    output = tmp_path / "report"

    _run(database, registry, output)
    first = _report(output)
    _run(database, registry, output)
    second = _report(output)

    assert "result: complete" in first
    assert "capability appended: 1" in first
    assert "entitlement appended: 1" in first
    assert "capability appended: 0" in second
    assert "entitlement appended: 0" in second
    assert "network access: 0" in second
    assert "AAPL" not in first + second
    assert "Private headline" not in first + second
    assert str(tmp_path) not in first + second
    assert stat.S_IMODE(registry.stat().st_mode) == 0o600
    assert stat.S_IMODE((output / cli.REPORT_NAME).stat().st_mode) == 0o600


def test_missing_run_and_path_collision_fail_before_registry_write(tmp_path: Path) -> None:
    registry = tmp_path / "registry.sqlite3"
    with pytest.raises(typer.BadParameter):
        _run(tmp_path / "missing.sqlite3", registry, tmp_path / "report")
    assert not registry.exists()

    database = _database(tmp_path / "alias")
    with pytest.raises(typer.BadParameter):
        _run(database, database, tmp_path / "alias-report")


def test_registry_cli_is_local_only() -> None:
    source = Path(cli.__file__).read_text(encoding="utf-8")

    assert "alpaca_news_client" not in source
    assert "alpaca_http" not in source
    assert "credentials" not in source
    assert "order" not in source.lower()


def _database(tmp_path: Path) -> Path:
    request = _request()
    database = tmp_path / "ledger" / "news.sqlite3"
    _ = collect_alpaca_news(
        _Fetcher(),
        AlpacaNewsStore(database),
        request,
        _clock=lambda: RECEIVED + dt.timedelta(seconds=1),
    )
    return database


def _request() -> AlpacaNewsRequest:
    return AlpacaNewsRequest(
        collection_id="capability-news-cli-001",
        symbols=("AAPL",),
        start_at=START,
        end_at=END,
        limit=50,
        max_pages=2,
    )


def _run(database: Path, registry: Path, output: Path) -> None:
    cli.main(
        collection_id="capability-news-cli-001",
        symbols="AAPL",
        start_at="2026-07-21T13:00:00Z",
        end_at="2026-07-21T14:00:00Z",
        database=str(database),
        registry=str(registry),
        output_dir=str(output),
        limit=50,
        max_pages=2,
    )


def _report(output: Path) -> str:
    return (output / cli.REPORT_NAME).read_text(encoding="utf-8")

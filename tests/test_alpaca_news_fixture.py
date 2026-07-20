from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pytest

from trading_agent.alpaca_news_fixture import (
    AlpacaNewsFixtureError,
    load_alpaca_news_fixture,
)
from trading_agent.alpaca_news_models import AlpacaNewsRequest

START = dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC)


def _request() -> AlpacaNewsRequest:
    return AlpacaNewsRequest(
        collection_id="fixture-news-001",
        symbols=("AAPL",),
        start_at=START,
        end_at=START + dt.timedelta(hours=1),
        limit=50,
        max_pages=2,
    )


def test_fixture_matches_exact_request_page_and_token(tmp_path: Path) -> None:
    request = _request()
    manifest = _manifest(tmp_path, request.request_id)
    fetcher = load_alpaca_news_fixture(manifest)

    first = fetcher.fetch_page(request, 0, None)
    second = fetcher.fetch_page(request, 1, "next-token")

    assert first.raw_payload == _payload(1, "next-token")
    assert second.raw_payload == _payload(2, None)
    assert first.request_id == request.request_id
    assert second.page_token == "next-token"


def test_fixture_rejects_request_or_token_mismatch(tmp_path: Path) -> None:
    request = _request()
    fetcher = load_alpaca_news_fixture(_manifest(tmp_path, request.request_id))
    different = request.model_copy(update={"collection_id": "fixture-news-002"})

    with pytest.raises(AlpacaNewsFixtureError):
        fetcher.fetch_page(different, 0, None)
    with pytest.raises(AlpacaNewsFixtureError):
        fetcher.fetch_page(request, 1, "wrong-token")


def test_fixture_rejects_symlinked_payload_and_noncontiguous_pages(tmp_path: Path) -> None:
    request = _request()
    manifest = _manifest(tmp_path, request.request_id)
    (tmp_path / "page-2.json").unlink()
    os.symlink(tmp_path / "page-1.json", tmp_path / "page-2.json")

    with pytest.raises(AlpacaNewsFixtureError):
        load_alpaca_news_fixture(manifest)

    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["responses"][1]["page_index"] = 2
    manifest.write_text(json.dumps(document), encoding="utf-8")
    (tmp_path / "page-2.json").unlink()
    (tmp_path / "page-2.json").write_bytes(_payload(2, None))

    with pytest.raises(AlpacaNewsFixtureError):
        load_alpaca_news_fixture(manifest)


def _manifest(directory: Path, request_id: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "page-1.json").write_bytes(_payload(1, "next-token"))
    (directory / "page-2.json").write_bytes(_payload(2, None))
    manifest = directory / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request_id": request_id,
                "responses": [
                    {
                        "page_index": 0,
                        "page_token": None,
                        "received_at": "2026-07-21T14:00:01Z",
                        "http_status": 200,
                        "content_type": "application/json",
                        "payload_path": "page-1.json",
                    },
                    {
                        "page_index": 1,
                        "page_token": "next-token",
                        "received_at": "2026-07-21T14:00:02Z",
                        "http_status": 200,
                        "content_type": "application/json",
                        "payload_path": "page-2.json",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _payload(article_id: int, token: str | None) -> bytes:
    return json.dumps(
        {
            "news": [
                {
                    "id": article_id,
                    "headline": f"Private issuer headline {article_id}",
                    "source": "benzinga",
                    "symbols": ["AAPL"],
                    "created_at": "2026-07-21T13:30:00Z",
                    "updated_at": f"2026-07-21T13:3{article_id - 1}:00Z",
                    "url": f"https://example.invalid/private/{article_id}",
                }
            ],
            "next_page_token": token,
        }
    ).encode()

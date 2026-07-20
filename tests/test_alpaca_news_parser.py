from __future__ import annotations

import datetime as dt
import gzip
import json
import zlib
from collections.abc import Callable

import pytest
from pydantic import JsonValue

from trading_agent.alpaca_news_models import (
    ALPACA_NEWS_MAX_RAW_BYTES,
    AlpacaNewsContractError,
    AlpacaNewsRawResponse,
    AlpacaNewsRequest,
)
from trading_agent.alpaca_news_parser import parse_alpaca_news_page

START = dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC)
END = START + dt.timedelta(hours=1)
RECEIVED = END + dt.timedelta(seconds=1)


def _request() -> AlpacaNewsRequest:
    return AlpacaNewsRequest(
        collection_id="news-cycle-001",
        symbols=("AAPL",),
        start_at=START,
        end_at=END,
        limit=50,
        max_pages=8,
    )


def _document(
    *,
    symbols: tuple[str, ...] = ("MSFT", "AAPL"),
    updated_at: str = "2026-07-21T13:31:00Z",
    next_page_token: str = "next-token",
    duplicate: bool = False,
) -> dict[str, JsonValue]:
    article: dict[str, JsonValue] = {
        "id": 12345,
        "headline": "Example issuer announces product launch",
        "source": "benzinga",
        "symbols": list(symbols),
        "created_at": "2026-07-21T13:30:00Z",
        "updated_at": updated_at,
        "url": "https://example.invalid/news/12345",
        "content": "licensed body is raw-only",
        "summary": "licensed summary is raw-only",
    }
    return {
        "news": [article, article] if duplicate else [article],
        "next_page_token": next_page_token,
    }


def _response(
    document: dict[str, JsonValue],
    *,
    content_encoding: str = "identity",
    raw_payload: bytes | None = None,
) -> AlpacaNewsRawResponse:
    return AlpacaNewsRawResponse(
        request_id=_request().request_id,
        page_index=0,
        page_token=None,
        received_at=RECEIVED,
        status_code=200,
        content_type="application/json",
        content_encoding=content_encoding,
        raw_payload=json.dumps(document).encode() if raw_payload is None else raw_payload,
    )


def test_parser_projects_metadata_without_licensed_content() -> None:
    page = parse_alpaca_news_page(_request(), _response(_document()))

    assert page.next_page_token == "next-token"
    assert len(page.articles) == 1
    article = page.articles[0]
    assert article.provider_article_id == 12345
    assert article.symbols == ("AAPL", "MSFT")
    assert article.headline == "Example issuer announces product launch"
    assert "licensed body" not in article.model_dump_json()
    assert len(article.event_id) == 64


@pytest.mark.parametrize(
    ("content_encoding", "compress"),
    (("gzip", gzip.compress), ("deflate", zlib.compress)),
)
def test_parser_decodes_supported_wire_compression(
    content_encoding: str,
    compress: Callable[[bytes], bytes],
) -> None:
    document = _document()
    encoded = compress(json.dumps(document).encode())

    page = parse_alpaca_news_page(
        _request(),
        _response(document, content_encoding=content_encoding, raw_payload=encoded),
    )

    assert page.articles[0].provider_article_id == 12345


def test_parser_rejects_unsupported_or_oversized_decoded_payload() -> None:
    document = _document()
    with pytest.raises(AlpacaNewsContractError):
        _ = parse_alpaca_news_page(
            _request(),
            _response(document, content_encoding="br"),
        )

    oversized = gzip.compress(b" " * (ALPACA_NEWS_MAX_RAW_BYTES + 1))
    with pytest.raises(AlpacaNewsContractError):
        _ = parse_alpaca_news_page(
            _request(),
            _response(document, content_encoding="gzip", raw_payload=oversized),
        )


def test_parser_rejects_article_without_requested_symbol() -> None:
    with pytest.raises(AlpacaNewsContractError):
        _ = parse_alpaca_news_page(_request(), _response(_document(symbols=("MSFT",))))


def test_parser_rejects_duplicate_provider_article_ids() -> None:
    with pytest.raises(AlpacaNewsContractError):
        _ = parse_alpaca_news_page(_request(), _response(_document(duplicate=True)))


def test_parser_rejects_provider_time_after_receipt() -> None:
    with pytest.raises(AlpacaNewsContractError):
        _ = parse_alpaca_news_page(
            _request(),
            _response(_document(updated_at="2026-07-21T14:00:02Z")),
        )


@pytest.mark.parametrize("token", ("", "bad\nvalue", "x" * 2049))
def test_parser_rejects_unsafe_next_page_token(token: str) -> None:
    with pytest.raises(AlpacaNewsContractError):
        _ = parse_alpaca_news_page(
            _request(),
            _response(_document(next_page_token=token)),
        )

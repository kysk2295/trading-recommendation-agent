from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import JsonValue

from trading_agent.alpaca_news_client import AlpacaNewsTransportError
from trading_agent.alpaca_news_collection import collect_alpaca_news
from trading_agent.alpaca_news_models import (
    AlpacaNewsFailure,
    AlpacaNewsRawResponse,
    AlpacaNewsRequest,
    AlpacaNewsRunStatus,
)
from trading_agent.alpaca_news_store import (
    AlpacaNewsStore,
    AlpacaNewsStoreError,
)

START = dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC)
END = START + dt.timedelta(hours=1)
RECEIVED_1 = END + dt.timedelta(seconds=1)
RECEIVED_2 = END + dt.timedelta(seconds=2)
COMPLETED = END + dt.timedelta(seconds=3)


def _request(*, max_pages: int = 8) -> AlpacaNewsRequest:
    return AlpacaNewsRequest(
        collection_id="news-collection-001",
        symbols=("AAPL",),
        start_at=START,
        end_at=END,
        limit=50,
        max_pages=max_pages,
    )


def _payload(article_id: int, token: str | None) -> bytes:
    document: dict[str, JsonValue] = {
        "news": [
            {
                "id": article_id,
                "headline": f"Issuer update {article_id}",
                "source": "benzinga",
                "symbols": ["AAPL"],
                "created_at": "2026-07-21T13:30:00Z",
                "updated_at": f"2026-07-21T13:3{article_id - 1}:00Z",
                "url": f"https://example.invalid/news/{article_id}",
            }
        ],
        "next_page_token": token,
    }
    return json.dumps(document).encode()


class _Fetcher:
    def __init__(
        self,
        pages: tuple[bytes, ...],
        *,
        status_code: int = 200,
    ) -> None:
        self.pages = pages
        self.status_code = status_code
        self.calls: list[tuple[int, str | None]] = []

    def fetch_page(
        self,
        request: AlpacaNewsRequest,
        page_index: int,
        page_token: str | None,
    ) -> AlpacaNewsRawResponse:
        self.calls.append((page_index, page_token))
        return AlpacaNewsRawResponse(
            request_id=request.request_id,
            page_index=page_index,
            page_token=page_token,
            received_at=RECEIVED_1 + dt.timedelta(seconds=page_index),
            status_code=self.status_code,
            content_type="application/json" if self.status_code == 200 else "text/plain",
            raw_payload=self.pages[page_index],
        )


class _FailingFetcher:
    calls = 0

    def fetch_page(
        self,
        request: AlpacaNewsRequest,
        page_index: int,
        page_token: str | None,
    ) -> AlpacaNewsRawResponse:
        _ = request, page_index, page_token
        self.calls += 1
        raise AlpacaNewsTransportError


def test_two_page_collection_persists_raw_and_success_terminal(tmp_path: Path) -> None:
    store = AlpacaNewsStore(tmp_path / "news" / "ledger.sqlite3")
    fetcher = _Fetcher((_payload(1, "next"), _payload(2, None)))

    result = collect_alpaca_news(
        fetcher,
        store,
        _request(),
        _clock=lambda: COMPLETED,
    )

    assert result.run.status is AlpacaNewsRunStatus.SUCCESS
    assert result.run.page_count == 2
    assert result.run.article_count == 2
    assert tuple(article.provider_article_id for article in result.articles) == (1, 2)
    assert fetcher.calls == [(0, None), (1, "next")]
    assert tuple(item.response.raw_payload for item in store.receipts(_request().request_id)) == (
        _payload(1, "next"),
        _payload(2, None),
    )
    assert store.run(_request().request_id) == result.run


def test_terminal_replay_never_calls_provider(tmp_path: Path) -> None:
    store = AlpacaNewsStore(tmp_path / "news" / "ledger.sqlite3")
    request = _request()
    first = collect_alpaca_news(
        _Fetcher((_payload(1, None),)),
        store,
        request,
        _clock=lambda: COMPLETED,
    )
    failing = _FailingFetcher()

    replay = collect_alpaca_news(failing, store, request, _clock=lambda: COMPLETED)

    assert replay.run == first.run
    assert replay.articles == first.articles
    assert replay.replayed is True
    assert failing.calls == 0
    assert store.counts() == (1, 1)


def test_orphan_receipt_resumes_only_missing_page(tmp_path: Path) -> None:
    store = AlpacaNewsStore(tmp_path / "news" / "ledger.sqlite3")
    request = _request()
    orphan = AlpacaNewsRawResponse(
        request_id=request.request_id,
        page_index=0,
        page_token=None,
        received_at=RECEIVED_1,
        status_code=200,
        content_type="application/json",
        raw_payload=_payload(1, "next"),
    )
    assert store.append_receipt(request, orphan) is True
    fetcher = _Fetcher((_payload(1, "next"), _payload(2, None)))

    result = collect_alpaca_news(fetcher, store, request, _clock=lambda: COMPLETED)

    assert result.run.status is AlpacaNewsRunStatus.SUCCESS
    assert result.run.article_count == 2
    assert fetcher.calls == [(1, "next")]


@pytest.mark.parametrize(
    ("fetcher", "failure"),
    (
        (_Fetcher((b"unavailable",), status_code=503), AlpacaNewsFailure.HTTP_STATUS),
        (_Fetcher((b"{}",)), AlpacaNewsFailure.RESPONSE_STRUCTURE),
        (_FailingFetcher(), AlpacaNewsFailure.TRANSPORT),
    ),
)
def test_failures_become_redacted_terminal(
    tmp_path: Path,
    fetcher: _Fetcher | _FailingFetcher,
    failure: AlpacaNewsFailure,
) -> None:
    store = AlpacaNewsStore(tmp_path / failure.value / "ledger.sqlite3")

    result = collect_alpaca_news(fetcher, store, _request(), _clock=lambda: COMPLETED)

    assert result.run.status is AlpacaNewsRunStatus.FAILED
    assert result.run.failure_code is failure
    assert result.run.article_count == 0
    expected_receipts = 0 if failure is AlpacaNewsFailure.TRANSPORT else 1
    assert store.counts() == (expected_receipts, 1)


def test_page_limit_preserves_partial_articles_as_failed_evidence(tmp_path: Path) -> None:
    store = AlpacaNewsStore(tmp_path / "news" / "ledger.sqlite3")

    result = collect_alpaca_news(
        _Fetcher((_payload(1, "next"),)),
        store,
        _request(max_pages=1),
        _clock=lambda: COMPLETED,
    )

    assert result.run.status is AlpacaNewsRunStatus.FAILED
    assert result.run.failure_code is AlpacaNewsFailure.PAGE_LIMIT
    assert result.run.article_count == 1


def test_repeated_page_token_fails_without_third_request(tmp_path: Path) -> None:
    store = AlpacaNewsStore(tmp_path / "news" / "ledger.sqlite3")
    fetcher = _Fetcher((_payload(1, "same"), _payload(2, "same")))

    result = collect_alpaca_news(fetcher, store, _request(), _clock=lambda: COMPLETED)

    assert result.run.failure_code is AlpacaNewsFailure.TOKEN_CYCLE
    assert fetcher.calls == [(0, None), (1, "same")]


def test_tampered_unrelated_receipt_blocks_all_reads(tmp_path: Path) -> None:
    store = AlpacaNewsStore(tmp_path / "news" / "ledger.sqlite3")
    first = _request()
    second = first.model_copy(update={"collection_id": "news-collection-002"})
    _ = collect_alpaca_news(
        _Fetcher((_payload(1, None),)),
        store,
        first,
        _clock=lambda: COMPLETED,
    )
    _ = collect_alpaca_news(
        _Fetcher((_payload(1, None),)),
        store,
        second,
        _clock=lambda: COMPLETED,
    )
    with sqlite3.connect(store.path) as connection:
        connection.executescript(
            "DROP TRIGGER alpaca_news_receipts_no_update;"
            "UPDATE alpaca_news_receipts SET raw_payload=X'00' "
            f"WHERE request_id='{first.request_id}';"
            "CREATE TRIGGER alpaca_news_receipts_no_update "
            "BEFORE UPDATE ON alpaca_news_receipts "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
        )

    with pytest.raises(AlpacaNewsStoreError):
        _ = store.run(second.request_id)

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from trading_agent.alpaca_news_collection import collect_alpaca_news
from trading_agent.alpaca_news_coverage import assess_alpaca_news_coverage
from trading_agent.alpaca_news_coverage_models import (
    AlpacaNewsCoverageArtifact,
    AlpacaNewsCoverageManifest,
    AlpacaNewsCoverageSlice,
    AlpacaNewsCoverageSliceStatus,
)
from trading_agent.alpaca_news_models import AlpacaNewsRawResponse, AlpacaNewsRequest
from trading_agent.alpaca_news_store import AlpacaNewsStore

START = dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC)
END = START + dt.timedelta(hours=1)
RECEIVED = END + dt.timedelta(seconds=1)
COMPLETED = END + dt.timedelta(seconds=2)


class _Fetcher:
    def __init__(self, symbol: str, *, status_code: int = 200) -> None:
        self.symbol = symbol
        self.status_code = status_code

    def fetch_page(
        self,
        request: AlpacaNewsRequest,
        page_index: int,
        page_token: str | None,
    ) -> AlpacaNewsRawResponse:
        payload = (
            _payload(self.symbol)
            if self.status_code == 200
            else b"provider unavailable"
        )
        return AlpacaNewsRawResponse(
            request_id=request.request_id,
            page_index=page_index,
            page_token=page_token,
            received_at=RECEIVED,
            status_code=self.status_code,
            content_type="application/json" if self.status_code == 200 else "text/plain",
            raw_payload=payload,
        )


def test_complete_assessment_counts_declared_symbols_and_accepted_articles(tmp_path: Path) -> None:
    first = _request("news-coverage-aapl", "AAPL")
    second = _request("news-coverage-msft", "MSFT")
    store = AlpacaNewsStore(tmp_path / "news" / "ledger.sqlite3")
    _collect(store, first, "AAPL")
    _collect(store, second, "MSFT")

    assessment = assess_alpaca_news_coverage(_manifest(first, second), store)

    assert assessment.complete is True
    assert assessment.declared_symbol_count == 2
    assert assessment.successful_symbol_count == 2
    assert assessment.completeness_bps == 10_000
    assert assessment.accepted_article_count == 2
    assert all(item.status is AlpacaNewsCoverageSliceStatus.SUCCESS for item in assessment.slices)


def test_assessment_preserves_failed_and_missing_slices_without_partial_evidence(
    tmp_path: Path,
) -> None:
    success = _request("news-coverage-success", "AAPL")
    failed = _request("news-coverage-failed", "MSFT")
    missing = _request("news-coverage-missing", "TSLA")
    store = AlpacaNewsStore(tmp_path / "news" / "ledger.sqlite3")
    _collect(store, success, "AAPL")
    _ = collect_alpaca_news(
        _Fetcher("MSFT", status_code=503),
        store,
        failed,
        _clock=lambda: COMPLETED,
    )

    assessment = assess_alpaca_news_coverage(_manifest(success, failed, missing), store)
    statuses = {item.request_id: item.status for item in assessment.slices}

    assert assessment.complete is False
    assert assessment.successful_symbol_count == 1
    assert assessment.completeness_bps == 3_333
    assert assessment.accepted_article_count == 1
    assert statuses[success.request_id] is AlpacaNewsCoverageSliceStatus.SUCCESS
    assert statuses[failed.request_id] is AlpacaNewsCoverageSliceStatus.FAILED
    assert statuses[missing.request_id] is AlpacaNewsCoverageSliceStatus.MISSING


def test_terminal_after_manifest_cutoff_is_missing_as_of_assessment(tmp_path: Path) -> None:
    first = _request("news-coverage-cutoff-a", "AAPL")
    second = _request("news-coverage-cutoff-b", "MSFT")
    store = AlpacaNewsStore(tmp_path / "news" / "ledger.sqlite3")
    _collect(store, first, "AAPL", completed_at=END + dt.timedelta(seconds=2))
    _collect(store, second, "MSFT", completed_at=END + dt.timedelta(seconds=4))
    manifest = _manifest(first, second, cutoff_at=END + dt.timedelta(seconds=3))

    assessment = assess_alpaca_news_coverage(manifest, store)
    statuses = {item.request_id: item.status for item in assessment.slices}

    assert statuses[first.request_id] is AlpacaNewsCoverageSliceStatus.SUCCESS
    assert statuses[second.request_id] is AlpacaNewsCoverageSliceStatus.MISSING
    assert assessment.successful_symbol_count == 1
    assert assessment.accepted_article_count == 1


def test_manifest_rejects_overlapping_symbols_or_different_windows() -> None:
    first = _request("news-coverage-invalid-a", "AAPL")
    overlap = _request("news-coverage-invalid-b", "AAPL")
    shifted = _request(
        "news-coverage-invalid-c",
        "MSFT",
        start_at=START + dt.timedelta(minutes=1),
    )

    with pytest.raises(ValueError):
        _ = _manifest(first, overlap)
    with pytest.raises(ValueError):
        _ = _manifest(first, shifted)


def test_coverage_artifact_rejects_foreign_slice_with_typed_contract_error(
    tmp_path: Path,
) -> None:
    request = _request("news-coverage-artifact", "AAPL")
    store = AlpacaNewsStore(tmp_path / "news" / "ledger.sqlite3")
    _collect(store, request, "AAPL")
    manifest = _manifest(request)
    assessment = assess_alpaca_news_coverage(manifest, store)
    foreign = AlpacaNewsCoverageSlice(
        request_id="f" * 64,
        status=AlpacaNewsCoverageSliceStatus.SUCCESS,
        run_id="e" * 64,
        completed_at=COMPLETED,
        page_count=1,
        article_count=1,
        latest_event_at=START + dt.timedelta(minutes=31),
        failure_code=None,
    )
    tampered = assessment.model_copy(update={"slices": (foreign,)})

    with pytest.raises(ValueError):
        _ = AlpacaNewsCoverageArtifact(manifest=manifest, assessment=tampered)


def _request(
    collection_id: str,
    symbol: str,
    *,
    start_at: dt.datetime = START,
) -> AlpacaNewsRequest:
    return AlpacaNewsRequest(
        collection_id=collection_id,
        symbols=(symbol,),
        start_at=start_at,
        end_at=END,
        limit=50,
        max_pages=2,
    )


def _manifest(
    *requests: AlpacaNewsRequest,
    cutoff_at: dt.datetime = END + dt.timedelta(seconds=3),
) -> AlpacaNewsCoverageManifest:
    return AlpacaNewsCoverageManifest(
        universe_id="us_news_bounded_fixture",
        cutoff_at=cutoff_at,
        requests=requests,
    )


def _collect(
    store: AlpacaNewsStore,
    request: AlpacaNewsRequest,
    symbol: str,
    *,
    completed_at: dt.datetime = COMPLETED,
) -> None:
    _ = collect_alpaca_news(
        _Fetcher(symbol),
        store,
        request,
        _clock=lambda: completed_at,
    )


def _payload(symbol: str) -> bytes:
    return json.dumps(
        {
            "news": [
                {
                    "id": 1 if symbol == "AAPL" else 2,
                    "headline": f"Synthetic {symbol} issuer update",
                    "source": "benzinga",
                    "symbols": [symbol],
                    "created_at": "2026-07-21T13:30:00Z",
                    "updated_at": "2026-07-21T13:31:00Z",
                    "url": f"https://example.invalid/{symbol.lower()}/1",
                }
            ],
            "next_page_token": None,
        }
    ).encode()

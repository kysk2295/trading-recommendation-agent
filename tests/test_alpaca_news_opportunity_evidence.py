from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from trading_agent.alpaca_news_collection import collect_alpaca_news
from trading_agent.alpaca_news_coverage import assess_alpaca_news_coverage
from trading_agent.alpaca_news_coverage_models import AlpacaNewsCoverageManifest
from trading_agent.alpaca_news_models import AlpacaNewsRawResponse, AlpacaNewsRequest
from trading_agent.alpaca_news_opportunity_evidence import (
    AlpacaNewsOpportunityEvidenceError,
    project_alpaca_news_opportunity_evidence,
)
from trading_agent.alpaca_news_opportunity_evidence_artifact import (
    load_alpaca_news_opportunity_evidence,
    publish_alpaca_news_opportunity_evidence,
)
from trading_agent.alpaca_news_store import AlpacaNewsStore

START = dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC)
END = START + dt.timedelta(hours=1)
RECEIVED = END + dt.timedelta(seconds=1)
COMPLETED = END + dt.timedelta(seconds=2)


class _Fetcher:
    def __init__(self, symbol: str, *, has_article: bool = True) -> None:
        self.symbol = symbol
        self.has_article = has_article

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
            raw_payload=_payload(self.symbol, has_article=self.has_article),
        )


def test_complete_coverage_projects_receipt_bound_metadata_without_licensed_text(
    tmp_path: Path,
) -> None:
    first = _request("news-evidence-aapl", "AAPL")
    second = _request("news-evidence-msft", "MSFT")
    store = AlpacaNewsStore(tmp_path / "news" / "ledger.sqlite3")
    _collect(store, first, "AAPL")
    _collect(store, second, "MSFT", has_article=False)
    manifest = _manifest(first, second)
    assessment = assess_alpaca_news_coverage(manifest, store)

    bundle = project_alpaca_news_opportunity_evidence(manifest, assessment, store)
    snapshots = {item.symbol: item for item in bundle.snapshots}

    assert tuple(snapshots) == ("AAPL", "MSFT")
    assert snapshots["AAPL"].coverage.record_count == 1
    assert snapshots["MSFT"].coverage.record_count == 0
    assert len(snapshots["MSFT"].evidence_refs) == 1
    observation = snapshots["AAPL"].observations[0]
    assert observation.received_at == RECEIVED
    assert observation.receipt_id == store.receipts(first.request_id)[0].response.receipt_id
    serialized = bundle.model_dump_json()
    assert "Synthetic private headline" not in serialized
    assert "example.invalid" not in serialized


def test_incomplete_coverage_cannot_become_opportunity_evidence(tmp_path: Path) -> None:
    first = _request("news-evidence-only", "AAPL")
    missing = _request("news-evidence-missing", "MSFT")
    store = AlpacaNewsStore(tmp_path / "news" / "ledger.sqlite3")
    _collect(store, first, "AAPL")
    manifest = _manifest(first, missing)
    assessment = assess_alpaca_news_coverage(manifest, store)

    with pytest.raises(AlpacaNewsOpportunityEvidenceError):
        _ = project_alpaca_news_opportunity_evidence(manifest, assessment, store)


def test_content_addressed_evidence_publication_is_idempotent_and_tamper_evident(
    tmp_path: Path,
) -> None:
    request = _request("news-evidence-artifact", "AAPL")
    store = AlpacaNewsStore(tmp_path / "news" / "ledger.sqlite3")
    _collect(store, request, "AAPL")
    manifest = _manifest(request)
    assessment = assess_alpaca_news_coverage(manifest, store)
    bundle = project_alpaca_news_opportunity_evidence(manifest, assessment, store)
    root = tmp_path / "evidence"

    path, created = publish_alpaca_news_opportunity_evidence(root, bundle)
    replay_path, replay_created = publish_alpaca_news_opportunity_evidence(root, bundle)

    assert created is True
    assert replay_created is False
    assert replay_path == path
    assert load_alpaca_news_opportunity_evidence(path) == bundle
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(AlpacaNewsOpportunityEvidenceError):
        _ = load_alpaca_news_opportunity_evidence(path)


def _request(collection_id: str, symbol: str) -> AlpacaNewsRequest:
    return AlpacaNewsRequest(
        collection_id=collection_id,
        symbols=(symbol,),
        start_at=START,
        end_at=END,
        limit=50,
        max_pages=2,
    )


def _manifest(*requests: AlpacaNewsRequest) -> AlpacaNewsCoverageManifest:
    return AlpacaNewsCoverageManifest(
        universe_id="us_news_evidence_fixture",
        cutoff_at=END + dt.timedelta(seconds=3),
        requests=requests,
    )


def _collect(
    store: AlpacaNewsStore,
    request: AlpacaNewsRequest,
    symbol: str,
    *,
    has_article: bool = True,
) -> None:
    _ = collect_alpaca_news(
        _Fetcher(symbol, has_article=has_article),
        store,
        request,
        _clock=lambda: COMPLETED,
    )


def _payload(symbol: str, *, has_article: bool) -> bytes:
    news = (
        [
            {
                "id": 1,
                "headline": "Synthetic private headline",
                "source": "benzinga",
                "symbols": [symbol],
                "created_at": "2026-07-21T13:30:00Z",
                "updated_at": "2026-07-21T13:31:00Z",
                "url": "https://example.invalid/private/1",
            }
        ]
        if has_article
        else []
    )
    return json.dumps({"news": news, "next_page_token": None}).encode()

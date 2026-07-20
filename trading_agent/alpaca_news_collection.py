from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, assert_never

from trading_agent.alpaca_news_client import AlpacaNewsTransportError
from trading_agent.alpaca_news_models import (
    AlpacaNewsArticle,
    AlpacaNewsFailure,
    AlpacaNewsRawResponse,
    AlpacaNewsRequest,
    AlpacaNewsRun,
    AlpacaNewsRunStatus,
)
from trading_agent.alpaca_news_replay import (
    AlpacaNewsReplayOutcome,
    AlpacaNewsReplayState,
    evaluate_alpaca_news_receipts,
    require_alpaca_news_run_projection,
)
from trading_agent.alpaca_news_store import AlpacaNewsStore


class AlpacaNewsFetcher(Protocol):
    def fetch_page(
        self,
        request: AlpacaNewsRequest,
        page_index: int,
        page_token: str | None,
    ) -> AlpacaNewsRawResponse: ...


@dataclass(frozen=True, slots=True)
class AlpacaNewsCollectionResult:
    run: AlpacaNewsRun
    articles: tuple[AlpacaNewsArticle, ...]
    replayed: bool


def collect_alpaca_news(
    fetcher: AlpacaNewsFetcher,
    store: AlpacaNewsStore,
    request: AlpacaNewsRequest,
    *,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> AlpacaNewsCollectionResult:
    existing = store.run(request.request_id)
    if existing is not None:
        if existing.request != request:
            raise AlpacaNewsTransportError
        receipts = tuple(item.response for item in store.receipts(request.request_id))
        articles = require_alpaca_news_run_projection(existing, receipts)
        return AlpacaNewsCollectionResult(existing, articles, True)
    store.preflight_write()
    started_at = _clock()
    receipts = [item.response for item in store.receipts(request.request_id)]
    while True:
        state = evaluate_alpaca_news_receipts(request, tuple(receipts))
        match state.outcome:
            case AlpacaNewsReplayOutcome.SUCCESS | AlpacaNewsReplayOutcome.FAILED:
                run = _run(request, receipts, state, started_at, _clock())
                _ = store.append_run(run)
                return AlpacaNewsCollectionResult(run, state.articles, False)
            case AlpacaNewsReplayOutcome.PENDING:
                pass
            case unreachable:
                assert_never(unreachable)
        try:
            response = fetcher.fetch_page(
                request,
                len(receipts),
                state.next_page_token,
            )
        except AlpacaNewsTransportError:
            run = _transport_run(request, receipts, state, started_at, _clock())
            _ = store.append_run(run)
            return AlpacaNewsCollectionResult(run, state.articles, False)
        _ = store.append_receipt(request, response)
        receipts.append(response)


def _run(
    request: AlpacaNewsRequest,
    receipts: list[AlpacaNewsRawResponse],
    state: AlpacaNewsReplayState,
    started_at: dt.datetime,
    completed_at: dt.datetime,
) -> AlpacaNewsRun:
    match state.outcome:
        case AlpacaNewsReplayOutcome.SUCCESS:
            status = AlpacaNewsRunStatus.SUCCESS
            failure = None
        case AlpacaNewsReplayOutcome.FAILED:
            status = AlpacaNewsRunStatus.FAILED
            failure = state.failure_code
        case AlpacaNewsReplayOutcome.PENDING:
            raise AlpacaNewsTransportError
        case unreachable:
            assert_never(unreachable)
    return _terminal(request, receipts, state.articles, started_at, completed_at, status, failure)


def _transport_run(
    request: AlpacaNewsRequest,
    receipts: list[AlpacaNewsRawResponse],
    state: AlpacaNewsReplayState,
    started_at: dt.datetime,
    completed_at: dt.datetime,
) -> AlpacaNewsRun:
    return _terminal(
        request,
        receipts,
        state.articles,
        started_at,
        completed_at,
        AlpacaNewsRunStatus.FAILED,
        AlpacaNewsFailure.TRANSPORT,
    )


def _terminal(
    request: AlpacaNewsRequest,
    receipts: list[AlpacaNewsRawResponse],
    articles: tuple[AlpacaNewsArticle, ...],
    started_at: dt.datetime,
    completed_at: dt.datetime,
    status: AlpacaNewsRunStatus,
    failure: AlpacaNewsFailure | None,
) -> AlpacaNewsRun:
    observed = tuple(receipt.received_at for receipt in receipts)
    return AlpacaNewsRun(
        request=request,
        started_at=min((started_at, *observed)),
        completed_at=max((completed_at, started_at, *observed)),
        status=status,
        failure_code=failure,
        receipt_ids=tuple(receipt.receipt_id for receipt in receipts),
        page_count=len(receipts),
        article_count=len(articles),
        latest_event_at=max((article.updated_at for article in articles), default=None),
    )

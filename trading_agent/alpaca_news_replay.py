from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

from trading_agent.alpaca_news_models import (
    AlpacaNewsArticle,
    AlpacaNewsContractError,
    AlpacaNewsFailure,
    AlpacaNewsRawResponse,
    AlpacaNewsRequest,
    AlpacaNewsRun,
    AlpacaNewsRunStatus,
)
from trading_agent.alpaca_news_parser import parse_alpaca_news_page


class AlpacaNewsReplayOutcome(StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AlpacaNewsReplayState:
    outcome: AlpacaNewsReplayOutcome
    articles: tuple[AlpacaNewsArticle, ...]
    next_page_token: str | None
    failure_code: AlpacaNewsFailure | None


def evaluate_alpaca_news_receipts(
    request: AlpacaNewsRequest,
    receipts: tuple[AlpacaNewsRawResponse, ...],
) -> AlpacaNewsReplayState:
    articles: list[AlpacaNewsArticle] = []
    provider_ids: set[int] = set()
    seen_tokens: set[str] = set()
    expected_token: str | None = None
    previous_received_at: dt.datetime | None = None
    for page_index, response in enumerate(receipts):
        if (
            response.request_id != request.request_id
            or response.page_index != page_index
            or response.page_token != expected_token
            or (previous_received_at is not None and response.received_at < previous_received_at)
        ):
            raise AlpacaNewsContractError
        previous_received_at = response.received_at
        if response.status_code != 200:
            if page_index != len(receipts) - 1:
                raise AlpacaNewsContractError
            return _failed(articles, AlpacaNewsFailure.HTTP_STATUS)
        try:
            page = parse_alpaca_news_page(request, response)
        except AlpacaNewsContractError:
            if page_index != len(receipts) - 1:
                raise AlpacaNewsContractError from None
            return _failed(articles, AlpacaNewsFailure.RESPONSE_STRUCTURE)
        page_ids = {article.provider_article_id for article in page.articles}
        if provider_ids.intersection(page_ids):
            return _failed(articles, AlpacaNewsFailure.DUPLICATE_ARTICLE)
        provider_ids.update(page_ids)
        articles.extend(page.articles)
        expected_token = page.next_page_token
        if expected_token is None:
            if page_index != len(receipts) - 1:
                raise AlpacaNewsContractError
            return AlpacaNewsReplayState(
                AlpacaNewsReplayOutcome.SUCCESS,
                tuple(articles),
                None,
                None,
            )
        if expected_token in seen_tokens:
            return _failed(articles, AlpacaNewsFailure.TOKEN_CYCLE)
        seen_tokens.add(expected_token)
    if len(receipts) >= request.max_pages:
        return _failed(articles, AlpacaNewsFailure.PAGE_LIMIT)
    return AlpacaNewsReplayState(
        AlpacaNewsReplayOutcome.PENDING,
        tuple(articles),
        expected_token,
        None,
    )


def require_alpaca_news_run_projection(
    run: AlpacaNewsRun,
    receipts: tuple[AlpacaNewsRawResponse, ...],
) -> tuple[AlpacaNewsArticle, ...]:
    state = evaluate_alpaca_news_receipts(run.request, receipts)
    match state.outcome:
        case AlpacaNewsReplayOutcome.SUCCESS:
            expected_status = AlpacaNewsRunStatus.SUCCESS
            expected_failure = None
        case AlpacaNewsReplayOutcome.FAILED:
            expected_status = AlpacaNewsRunStatus.FAILED
            expected_failure = state.failure_code
        case AlpacaNewsReplayOutcome.PENDING:
            expected_status = AlpacaNewsRunStatus.FAILED
            expected_failure = AlpacaNewsFailure.TRANSPORT
        case unreachable:
            assert_never(unreachable)
    latest = max((article.updated_at for article in state.articles), default=None)
    if (
        run.status is not expected_status
        or run.failure_code is not expected_failure
        or run.receipt_ids != tuple(receipt.receipt_id for receipt in receipts)
        or run.page_count != len(receipts)
        or run.article_count != len(state.articles)
        or run.latest_event_at != latest
        or any(not run.started_at <= receipt.received_at <= run.completed_at for receipt in receipts)
    ):
        raise AlpacaNewsContractError
    return state.articles


def _failed(
    articles: list[AlpacaNewsArticle],
    failure: AlpacaNewsFailure,
) -> AlpacaNewsReplayState:
    return AlpacaNewsReplayState(
        AlpacaNewsReplayOutcome.FAILED,
        tuple(articles),
        None,
        failure,
    )

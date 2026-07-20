from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trading_agent.alpaca_news_models import (
    AlpacaNewsArticle,
    AlpacaNewsContractError,
    AlpacaNewsPage,
    AlpacaNewsRawResponse,
    AlpacaNewsRequest,
)


class _WireArticle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: int
    headline: str
    source: str
    symbols: tuple[str, ...]
    created_at: dt.datetime
    updated_at: dt.datetime
    url: str


class _WirePage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    news: tuple[_WireArticle, ...] = Field(max_length=50)
    next_page_token: str | None = None


def parse_alpaca_news_page(
    request: AlpacaNewsRequest,
    response: AlpacaNewsRawResponse,
) -> AlpacaNewsPage:
    try:
        if response.request_id != request.request_id or response.status_code != 200:
            raise AlpacaNewsContractError
        wire = _WirePage.model_validate_json(response.raw_payload)
        articles = tuple(
            AlpacaNewsArticle(
                provider_article_id=item.id,
                headline=item.headline,
                source=item.source,
                symbols=item.symbols,
                created_at=item.created_at,
                updated_at=item.updated_at,
                url=item.url,
            )
            for item in wire.news
        )
        requested = frozenset(request.symbols)
        if any(
            not requested.intersection(article.symbols)
            or article.updated_at < request.start_at
            or article.updated_at > request.end_at
            or article.updated_at > response.received_at
            for article in articles
        ):
            raise AlpacaNewsContractError
        return AlpacaNewsPage(
            articles=articles,
            next_page_token=wire.next_page_token,
        )
    except (TypeError, ValidationError, ValueError):
        raise AlpacaNewsContractError from None

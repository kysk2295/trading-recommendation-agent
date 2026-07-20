from __future__ import annotations

import datetime as dt
import zlib

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trading_agent.alpaca_news_models import (
    ALPACA_NEWS_MAX_RAW_BYTES,
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
        wire = _WirePage.model_validate_json(_decoded_payload(response))
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


def _decoded_payload(response: AlpacaNewsRawResponse) -> bytes:
    if response.content_encoding == "identity":
        return response.raw_payload
    if response.content_encoding not in {"gzip", "deflate"}:
        raise AlpacaNewsContractError
    window_bits = zlib.MAX_WBITS | 16 if response.content_encoding == "gzip" else zlib.MAX_WBITS
    try:
        decoder = zlib.decompressobj(window_bits)
        payload = decoder.decompress(response.raw_payload, ALPACA_NEWS_MAX_RAW_BYTES + 1)
        if len(payload) > ALPACA_NEWS_MAX_RAW_BYTES:
            raise AlpacaNewsContractError
        payload += decoder.flush(ALPACA_NEWS_MAX_RAW_BYTES + 1 - len(payload))
    except zlib.error:
        raise AlpacaNewsContractError from None
    if (
        len(payload) > ALPACA_NEWS_MAX_RAW_BYTES
        or not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
    ):
        raise AlpacaNewsContractError
    return payload

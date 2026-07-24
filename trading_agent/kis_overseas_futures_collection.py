from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from trading_agent.kis_overseas_futures_client import (
    KisOverseasFuturesTransportError,
)
from trading_agent.kis_overseas_futures_models import (
    KisFuturesQuote,
    KisFuturesQuoteError,
    KisFuturesQuoteFailure,
    KisFuturesQuoteRawResponse,
    KisFuturesQuoteRequest,
    KisFuturesQuoteRun,
    KisFuturesQuoteStatus,
)
from trading_agent.kis_overseas_futures_parser import (
    parse_kis_overseas_futures_quote,
)
from trading_agent.kis_overseas_futures_store import KisOverseasFuturesStore


class KisOverseasFuturesFetcher(Protocol):
    def fetch(
        self,
        request: KisFuturesQuoteRequest,
        symbol: str,
    ) -> KisFuturesQuoteRawResponse: ...


@dataclass(frozen=True, slots=True)
class KisFuturesQuoteCollectionResult:
    run: KisFuturesQuoteRun
    replayed: bool


def collect_kis_overseas_futures_quotes(
    fetcher: KisOverseasFuturesFetcher,
    store: KisOverseasFuturesStore,
    request: KisFuturesQuoteRequest,
    *,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> KisFuturesQuoteCollectionResult:
    existing = store.run(request.request_id)
    if existing is not None:
        return KisFuturesQuoteCollectionResult(existing, True)
    store.preflight_write()
    started_at = _clock()
    quotes: list[KisFuturesQuote] = []
    receipt_ids: list[str] = []
    failure: KisFuturesQuoteFailure | None = None
    for symbol in request.symbols:
        try:
            response = fetcher.fetch(request, symbol)
        except KisOverseasFuturesTransportError:
            failure = KisFuturesQuoteFailure.TRANSPORT
            break
        _ = store.append_receipt(request, response)
        receipt_ids.append(response.receipt_id)
        if response.status_code != 200:
            failure = KisFuturesQuoteFailure.HTTP_STATUS
            break
        try:
            quotes.append(
                parse_kis_overseas_futures_quote(request, response)
            )
        except KisFuturesQuoteError as error:
            failure = error.failure
            break
    success = failure is None and len(quotes) == len(request.symbols)
    run = KisFuturesQuoteRun(
        request=request,
        started_at=started_at,
        completed_at=_clock(),
        status=(
            KisFuturesQuoteStatus.SUCCESS
            if success
            else KisFuturesQuoteStatus.FAILED
        ),
        failure=None if success else failure,
        receipt_ids=tuple(receipt_ids),
        quotes=tuple(quotes) if success else (),
    )
    _ = store.append_run(run)
    return KisFuturesQuoteCollectionResult(run, False)


__all__ = (
    "KisFuturesQuoteCollectionResult",
    "KisOverseasFuturesFetcher",
    "collect_kis_overseas_futures_quotes",
)

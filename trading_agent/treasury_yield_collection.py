from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, override

from trading_agent.treasury_yield_models import (
    TreasuryYieldError,
    TreasuryYieldFailure,
    TreasuryYieldRawResponse,
    TreasuryYieldRequest,
    TreasuryYieldRun,
    TreasuryYieldStatus,
)
from trading_agent.treasury_yield_parser import parse_treasury_yield_context
from trading_agent.treasury_yield_store import TreasuryYieldStore


class TreasuryYieldFetcher(Protocol):
    def fetch(
        self,
        request: TreasuryYieldRequest,
    ) -> TreasuryYieldRawResponse: ...


class TreasuryYieldTransportError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Treasury yield transport failed"


@dataclass(frozen=True, slots=True)
class TreasuryYieldCollectionResult:
    run: TreasuryYieldRun
    replayed: bool


def collect_treasury_yield(
    fetcher: TreasuryYieldFetcher,
    store: TreasuryYieldStore,
    request: TreasuryYieldRequest,
    *,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> TreasuryYieldCollectionResult:
    existing = store.run(request.request_id)
    if existing is not None:
        return TreasuryYieldCollectionResult(existing, True)
    started_at = _clock()
    try:
        response = fetcher.fetch(request)
    except TreasuryYieldTransportError:
        run = TreasuryYieldRun(
            request=request,
            started_at=started_at,
            completed_at=_clock(),
            status=TreasuryYieldStatus.FAILED,
            failure=TreasuryYieldFailure.TRANSPORT,
            receipt_id=None,
            context=None,
        )
        _ = store.append_run(run)
        return TreasuryYieldCollectionResult(run, False)
    _ = store.append_receipt(request, response)
    context = None
    failure = None
    if response.status_code != 200:
        failure = TreasuryYieldFailure.HTTP_STATUS
    else:
        try:
            context = parse_treasury_yield_context(request, response)
        except TreasuryYieldError:
            failure = TreasuryYieldFailure.RESPONSE_STRUCTURE
    run = TreasuryYieldRun(
        request=request,
        started_at=started_at,
        completed_at=_clock(),
        status=(TreasuryYieldStatus.SUCCESS if failure is None else TreasuryYieldStatus.FAILED),
        failure=failure,
        receipt_id=response.receipt_id,
        context=context,
    )
    _ = store.append_run(run)
    return TreasuryYieldCollectionResult(run, False)


__all__ = (
    "TreasuryYieldCollectionResult",
    "TreasuryYieldFetcher",
    "TreasuryYieldTransportError",
    "collect_treasury_yield",
)

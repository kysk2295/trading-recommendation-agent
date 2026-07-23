from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, override

from trading_agent.cftc_tff_models import (
    CftcTffError,
    CftcTffFailure,
    CftcTffRawResponse,
    CftcTffRequest,
    CftcTffRun,
    CftcTffStatus,
)
from trading_agent.cftc_tff_parser import parse_cftc_tff_context
from trading_agent.cftc_tff_store import CftcTffStore


class CftcTffFetcher(Protocol):
    def fetch(self, request: CftcTffRequest) -> CftcTffRawResponse: ...


class CftcTffTransportError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "CFTC TFF transport failed"


@dataclass(frozen=True, slots=True)
class CftcTffCollectionResult:
    run: CftcTffRun
    replayed: bool


def collect_cftc_tff(
    fetcher: CftcTffFetcher,
    store: CftcTffStore,
    request: CftcTffRequest,
    *,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> CftcTffCollectionResult:
    existing = store.run(request.request_id)
    if existing is not None:
        return CftcTffCollectionResult(existing, True)
    started_at = _clock()
    try:
        response = fetcher.fetch(request)
    except CftcTffTransportError:
        run = CftcTffRun(
            request=request,
            started_at=started_at,
            completed_at=_clock(),
            status=CftcTffStatus.FAILED,
            failure=CftcTffFailure.TRANSPORT,
            receipt_id=None,
            context=None,
        )
        _ = store.append_run(run)
        return CftcTffCollectionResult(run, False)
    _ = store.append_receipt(request, response)
    context = None
    failure = None
    if response.status_code != 200:
        failure = CftcTffFailure.HTTP_STATUS
    else:
        try:
            context = parse_cftc_tff_context(request, response)
        except CftcTffError:
            failure = CftcTffFailure.RESPONSE_STRUCTURE
    run = CftcTffRun(
        request=request,
        started_at=started_at,
        completed_at=_clock(),
        status=(CftcTffStatus.SUCCESS if failure is None else CftcTffStatus.FAILED),
        failure=failure,
        receipt_id=response.receipt_id,
        context=context,
    )
    _ = store.append_run(run)
    return CftcTffCollectionResult(run, False)


__all__ = (
    "CftcTffCollectionResult",
    "CftcTffFetcher",
    "CftcTffTransportError",
    "collect_cftc_tff",
)

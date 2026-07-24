from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, override

from trading_agent.bls_public_models import (
    BlsPublicError,
    BlsPublicFailure,
    BlsPublicRawResponse,
    BlsPublicRequest,
    BlsPublicRun,
    BlsPublicStatus,
)
from trading_agent.bls_public_parser import parse_bls_macro_snapshot
from trading_agent.bls_public_store import BlsPublicStore


class BlsPublicFetcher(Protocol):
    def fetch(self, request: BlsPublicRequest) -> BlsPublicRawResponse: ...


class BlsPublicTransportError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "BLS public data transport failed"


@dataclass(frozen=True, slots=True)
class BlsPublicCollectionResult:
    run: BlsPublicRun
    replayed: bool
    fetched: bool


def collect_bls_public_data(
    fetcher: BlsPublicFetcher,
    store: BlsPublicStore,
    request: BlsPublicRequest,
    *,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> BlsPublicCollectionResult:
    existing = store.run(request.request_id)
    if existing is not None:
        return BlsPublicCollectionResult(existing, True, False)
    started_at = _clock()
    response = store.receipt(request.request_id)
    fetched = response is None
    if response is None:
        try:
            response = fetcher.fetch(request)
        except BlsPublicTransportError:
            run = BlsPublicRun(
                request=request,
                started_at=started_at,
                completed_at=_clock(),
                status=BlsPublicStatus.FAILED,
                failure=BlsPublicFailure.TRANSPORT,
                receipt_id=None,
                snapshot=None,
            )
            _ = store.append_run(run)
            return BlsPublicCollectionResult(run, False, True)
        _ = store.append_receipt(request, response)
    else:
        started_at = response.received_at
    snapshot = None
    failure = None
    if response.status_code != 200:
        failure = BlsPublicFailure.HTTP_STATUS
    else:
        try:
            snapshot = parse_bls_macro_snapshot(request, response)
        except BlsPublicError:
            failure = BlsPublicFailure.RESPONSE_STRUCTURE
    run = BlsPublicRun(
        request=request,
        started_at=started_at,
        completed_at=_clock(),
        status=(
            BlsPublicStatus.SUCCESS
            if failure is None
            else BlsPublicStatus.FAILED
        ),
        failure=failure,
        receipt_id=response.receipt_id,
        snapshot=snapshot,
    )
    _ = store.append_run(run)
    return BlsPublicCollectionResult(run, False, fetched)


__all__ = (
    "BlsPublicCollectionResult",
    "BlsPublicFetcher",
    "BlsPublicTransportError",
    "collect_bls_public_data",
)

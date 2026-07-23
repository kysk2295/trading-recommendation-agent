from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from trading_agent.alpaca_option_chain_client import (
    AlpacaOptionChainTransportError,
)
from trading_agent.alpaca_option_chain_models import (
    OptionChainFailure,
    OptionChainRawResponse,
    OptionChainRequest,
    OptionChainRun,
    OptionChainStatus,
    OptionContractSnapshot,
)
from trading_agent.alpaca_option_chain_projection import (
    merge_option_chain_page,
    parse_option_chain_page,
)
from trading_agent.alpaca_option_chain_store import AlpacaOptionChainStore


class OptionChainPageFetcher(Protocol):
    def fetch_page(
        self,
        request: OptionChainRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionChainRawResponse: ...


@dataclass(frozen=True, slots=True)
class OptionChainCollectionResult:
    run: OptionChainRun
    replayed: bool


def collect_alpaca_option_chain(
    fetcher: OptionChainPageFetcher,
    store: AlpacaOptionChainStore,
    request: OptionChainRequest,
    *,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> OptionChainCollectionResult:
    existing_run = store.run(request.request_id)
    if existing_run is not None:
        return OptionChainCollectionResult(existing_run, True)
    started_at = _clock()
    receipts = list(store.receipts(request.request_id))
    snapshots: dict[str, OptionContractSnapshot] = {}
    seen_tokens: set[str] = set()
    page_token: str | None = None
    page_index = 0
    for response in receipts:
        if response.page_index != page_index or response.page_token != page_token:
            return _finish(
                store,
                request,
                receipts,
                snapshots,
                started_at,
                _clock(),
                OptionChainFailure.RESPONSE_STRUCTURE,
            )
        parsed = parse_option_chain_page(request, response)
        if isinstance(parsed, OptionChainFailure):
            return _finish(
                store,
                request,
                receipts,
                snapshots,
                started_at,
                _clock(),
                parsed,
            )
        failure = merge_option_chain_page(snapshots, parsed)
        if failure is not None:
            return _finish(
                store,
                request,
                receipts,
                snapshots,
                started_at,
                _clock(),
                failure,
            )
        page_token = parsed.next_page_token
        if page_token is None:
            return _finish(
                store,
                request,
                receipts,
                snapshots,
                started_at,
                _clock(),
                None,
            )
        if page_token in seen_tokens:
            return _finish(
                store,
                request,
                receipts,
                snapshots,
                started_at,
                _clock(),
                OptionChainFailure.TOKEN_CYCLE,
            )
        seen_tokens.add(page_token)
        page_index += 1
    while page_index < request.max_pages:
        try:
            response = fetcher.fetch_page(request, page_index, page_token)
        except AlpacaOptionChainTransportError:
            return _finish(
                store,
                request,
                receipts,
                snapshots,
                started_at,
                _clock(),
                OptionChainFailure.TRANSPORT,
            )
        _ = store.append_receipt(request, response)
        receipts.append(response)
        parsed = parse_option_chain_page(request, response)
        if isinstance(parsed, OptionChainFailure):
            return _finish(
                store,
                request,
                receipts,
                snapshots,
                started_at,
                _clock(),
                parsed,
            )
        failure = merge_option_chain_page(snapshots, parsed)
        if failure is not None:
            return _finish(
                store,
                request,
                receipts,
                snapshots,
                started_at,
                _clock(),
                failure,
            )
        page_token = parsed.next_page_token
        if page_token is None:
            return _finish(
                store,
                request,
                receipts,
                snapshots,
                started_at,
                _clock(),
                None,
            )
        if page_token in seen_tokens:
            return _finish(
                store,
                request,
                receipts,
                snapshots,
                started_at,
                _clock(),
                OptionChainFailure.TOKEN_CYCLE,
            )
        seen_tokens.add(page_token)
        page_index += 1
    return _finish(
        store,
        request,
        receipts,
        snapshots,
        started_at,
        _clock(),
        OptionChainFailure.PAGE_LIMIT,
    )


def _finish(
    store: AlpacaOptionChainStore,
    request: OptionChainRequest,
    receipts: list[OptionChainRawResponse],
    snapshots: dict[str, OptionContractSnapshot],
    started_at: dt.datetime,
    completed_at: dt.datetime,
    failure: OptionChainFailure | None,
) -> OptionChainCollectionResult:
    run = OptionChainRun(
        request=request,
        started_at=started_at,
        completed_at=completed_at,
        status=(
            OptionChainStatus.SUCCESS
            if failure is None
            else OptionChainStatus.FAILED
        ),
        failure_code=failure,
        receipt_ids=tuple(item.receipt_id for item in receipts),
        snapshots=tuple(snapshots[key] for key in sorted(snapshots)),
    )
    _ = store.append_run(run)
    return OptionChainCollectionResult(run, False)


__all__ = (
    "OptionChainCollectionResult",
    "OptionChainPageFetcher",
    "collect_alpaca_option_chain",
)

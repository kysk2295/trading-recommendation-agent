from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, assert_never

from trading_agent.alpaca_option_contract_models import (
    OptionCatalogFailure,
    OptionCatalogStatus,
    OptionContractCatalogRequest,
    OptionContractCatalogRun,
    OptionContractRawResponse,
)
from trading_agent.alpaca_option_contract_page import (
    merge_option_contract_page,
    parse_option_contract_page,
)
from trading_agent.alpaca_option_contract_projection import (
    project_option_security_master_contract,
)
from trading_agent.alpaca_option_contract_provider_models import (
    ProviderOptionContract,
    ProviderOptionContractPage,
)
from trading_agent.alpaca_option_contract_store import AlpacaOptionContractStore


class OptionContractPageFetcher(Protocol):
    def fetch_page(
        self,
        request: OptionContractCatalogRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionContractRawResponse: ...


class AlpacaOptionContractTransportError(RuntimeError):
    def __str__(self) -> str:
        return "Alpaca option contract transport failed"


@dataclass(frozen=True, slots=True)
class OptionContractCollectionResult:
    run: OptionContractCatalogRun
    replayed: bool


class _CollectionState:
    __slots__ = (
        "contracts",
        "receipts",
        "request",
        "started_at",
        "store",
        "symbols",
    )

    def __init__(
        self,
        store: AlpacaOptionContractStore,
        request: OptionContractCatalogRequest,
        started_at: dt.datetime,
    ) -> None:
        self.store = store
        self.request = request
        self.started_at = started_at
        self.receipts: list[OptionContractRawResponse] = []
        self.contracts: dict[str, ProviderOptionContract] = {}
        self.symbols: set[str] = set()


def collect_alpaca_option_contracts(
    fetcher: OptionContractPageFetcher,
    store: AlpacaOptionContractStore,
    request: OptionContractCatalogRequest,
    *,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> OptionContractCollectionResult:
    existing = store.run(request.request_id)
    if existing is not None:
        return OptionContractCollectionResult(existing, True)
    state = _CollectionState(store, request, _clock())
    state.receipts.extend(store.receipts(request.request_id))
    seen_tokens: set[str] = set()
    page_token: str | None = None
    page_index = 0
    for response in state.receipts:
        if response.page_index != page_index or response.page_token != page_token:
            return _finish(
                state,
                _clock(),
                OptionCatalogFailure.RESPONSE_STRUCTURE,
            )
        parsed = parse_option_contract_page(request, response)
        match parsed:
            case OptionCatalogFailure() as failure:
                return _finish(
                    state,
                    _clock(),
                    failure,
                )
            case ProviderOptionContractPage() as page:
                failure = merge_option_contract_page(
                    state.contracts,
                    state.symbols,
                    page,
                )
            case unreachable:
                assert_never(unreachable)
        if failure is not None:
            return _finish(
                state,
                _clock(),
                failure,
            )
        page_token = page.page_token
        if page_token is None:
            return _finish(
                state,
                _clock(),
                None,
            )
        if page_token in seen_tokens:
            return _finish(
                state,
                _clock(),
                OptionCatalogFailure.TOKEN_CYCLE,
            )
        seen_tokens.add(page_token)
        page_index += 1
    while page_index < request.max_pages:
        try:
            response = fetcher.fetch_page(request, page_index, page_token)
        except AlpacaOptionContractTransportError:
            return _finish(
                state,
                _clock(),
                OptionCatalogFailure.TRANSPORT,
            )
        _ = store.append_receipt(request, response)
        state.receipts.append(response)
        parsed = parse_option_contract_page(request, response)
        match parsed:
            case OptionCatalogFailure() as failure:
                return _finish(
                    state,
                    _clock(),
                    failure,
                )
            case ProviderOptionContractPage() as page:
                failure = merge_option_contract_page(
                    state.contracts,
                    state.symbols,
                    page,
                )
            case unreachable:
                assert_never(unreachable)
        if failure is not None:
            return _finish(
                state,
                _clock(),
                failure,
            )
        page_token = page.page_token
        if page_token is None:
            return _finish(
                state,
                _clock(),
                None,
            )
        if page_token in seen_tokens:
            return _finish(
                state,
                _clock(),
                OptionCatalogFailure.TOKEN_CYCLE,
            )
        seen_tokens.add(page_token)
        page_index += 1
    return _finish(
        state,
        _clock(),
        OptionCatalogFailure.PAGE_LIMIT,
    )


def _finish(
    state: _CollectionState,
    completed_at: dt.datetime,
    failure: OptionCatalogFailure | None,
) -> OptionContractCollectionResult:
    terminal_failure = (
        OptionCatalogFailure.EMPTY_RESULT
        if failure is None and not state.contracts
        else failure
    )
    try:
        projected = tuple(
            sorted(
                (
                    project_option_security_master_contract(item, completed_at)
                    for item in state.contracts.values()
                ),
                key=lambda item: item.instrument.value,
            )
        )
    except ValueError:
        projected = ()
        terminal_failure = OptionCatalogFailure.RESPONSE_STRUCTURE
    run = OptionContractCatalogRun(
        request=state.request,
        started_at=state.started_at,
        completed_at=completed_at,
        status=(
            OptionCatalogStatus.SUCCESS
            if terminal_failure is None
            else OptionCatalogStatus.FAILED
        ),
        failure_code=terminal_failure,
        receipt_ids=tuple(item.receipt_id for item in state.receipts),
        contracts=projected,
    )
    _ = state.store.append_run(run)
    return OptionContractCollectionResult(run, False)


__all__ = (
    "AlpacaOptionContractTransportError",
    "OptionContractCollectionResult",
    "OptionContractPageFetcher",
    "collect_alpaca_option_contracts",
)

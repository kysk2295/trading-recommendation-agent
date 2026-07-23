from __future__ import annotations

from pydantic import ValidationError

from trading_agent.alpaca_option_chain_models import (
    AlpacaOptionChainError,
    OptionChainFailure,
    OptionChainRawResponse,
    OptionChainRequest,
    OptionContractSnapshot,
    ProviderOptionChainPage,
    option_snapshot,
)


def parse_option_chain_page(
    request: OptionChainRequest,
    response: OptionChainRawResponse,
) -> ProviderOptionChainPage | OptionChainFailure:
    if response.status_code != 200:
        return OptionChainFailure.HTTP_STATUS
    try:
        page = ProviderOptionChainPage.model_validate_json(response.raw_payload)
        if len(page.snapshots) > request.limit:
            raise AlpacaOptionChainError
        checked = tuple(
            option_snapshot(symbol, value)
            for symbol, value in page.snapshots.items()
        )
        if any(
            item.underlying_symbol != request.underlying_symbol
            or item.expiration_date != request.expiration_date
            or item.contract_type is not request.contract_type
            for item in checked
        ):
            raise AlpacaOptionChainError
        return page
    except (AlpacaOptionChainError, TypeError, ValidationError, ValueError):
        return OptionChainFailure.RESPONSE_STRUCTURE


def merge_option_chain_page(
    snapshots: dict[str, OptionContractSnapshot],
    page: ProviderOptionChainPage,
) -> OptionChainFailure | None:
    for symbol, value in page.snapshots.items():
        if symbol in snapshots:
            return OptionChainFailure.DUPLICATE_CONTRACT
        snapshots[symbol] = option_snapshot(symbol, value)
    return None


__all__ = ("merge_option_chain_page", "parse_option_chain_page")

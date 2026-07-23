from __future__ import annotations

from pydantic import ValidationError

from trading_agent.alpaca_option_contract_models import (
    AlpacaOptionContractError,
    OptionCatalogFailure,
    OptionContractCatalogRequest,
    OptionContractRawResponse,
)
from trading_agent.alpaca_option_contract_provider_models import (
    ProviderOptionContract,
    ProviderOptionContractPage,
)


def parse_option_contract_page(
    request: OptionContractCatalogRequest,
    response: OptionContractRawResponse,
) -> ProviderOptionContractPage | OptionCatalogFailure:
    if response.status_code != 200:
        return OptionCatalogFailure.HTTP_STATUS
    try:
        page = ProviderOptionContractPage.model_validate_json(
            response.raw_payload
        )
        if (
            len(page.option_contracts) > request.limit
            or (
                page.limit is not None
                and page.limit != request.limit
            )
            or any(
                item.underlying_symbol != request.underlying_symbol
                or item.expiration_date != request.expiration_date
                or item.type is not request.contract_type
                for item in page.option_contracts
            )
        ):
            raise AlpacaOptionContractError
        return page
    except (
        AlpacaOptionContractError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        return OptionCatalogFailure.RESPONSE_STRUCTURE


def merge_option_contract_page(
    contracts: dict[str, ProviderOptionContract],
    symbols: set[str],
    page: ProviderOptionContractPage,
) -> OptionCatalogFailure | None:
    for contract in page.option_contracts:
        key = str(contract.id)
        if key in contracts or contract.symbol in symbols:
            return OptionCatalogFailure.DUPLICATE_CONTRACT
        contracts[key] = contract
        symbols.add(contract.symbol)
    return None


__all__ = ("merge_option_contract_page", "parse_option_contract_page")

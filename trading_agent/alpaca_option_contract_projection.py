from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Final

from trading_agent.alpaca_option_chain_models import OptionContractType
from trading_agent.alpaca_option_contract_models import (
    AlpacaOptionContractError,
    OptionSecurityMasterContract,
)
from trading_agent.alpaca_option_contract_provider_models import (
    ProviderOptionContract,
    ProviderOptionContractStatus,
)
from trading_agent.security_master_models import (
    AssetClass,
    DataMarketDomain,
    InstrumentAlias,
    InstrumentAliasType,
    InstrumentId,
)

_OPTION_SYMBOL: Final = re.compile(
    r"^([A-Z]{1,6})([0-9]{6})([CP])([0-9]{8})$"
)
_RIGHTS: Final = {
    "C": OptionContractType.CALL,
    "P": OptionContractType.PUT,
}


def project_option_security_master_contract(
    contract: ProviderOptionContract,
    observed_at: dt.datetime,
) -> OptionSecurityMasterContract:
    matched = _OPTION_SYMBOL.fullmatch(contract.symbol)
    if matched is None:
        raise AlpacaOptionContractError
    root, date_text, right, strike_text = matched.groups()
    contract_type = _RIGHTS[right]
    parsed_expiration = dt.datetime.strptime(date_text, "%y%m%d").date()
    parsed_strike = Decimal(strike_text) / Decimal(1_000)
    if (
        contract.status is not ProviderOptionContractStatus.ACTIVE
        or root != contract.root_symbol
        or parsed_expiration != contract.expiration_date
        or parsed_strike != contract.strike_price
        or contract_type is not contract.type
        or (
            contract.open_interest_date is not None
            and contract.open_interest_date > observed_at.date()
        )
        or (
            contract.close_price_date is not None
            and contract.close_price_date > observed_at.date()
        )
    ):
        raise AlpacaOptionContractError
    instrument_id = f"alpaca:{contract.id}"
    instrument = InstrumentId(
        value=instrument_id,
        market_domain=DataMarketDomain.US_DERIVATIVES,
        asset_class=AssetClass.OPTION,
        venue="US_OPTIONS",
        currency="USD",
        timezone="America/New_York",
        valid_from=observed_at,
    )
    alias = InstrumentAlias(
        instrument_id=instrument_id,
        namespace="alpaca",
        alias_type=InstrumentAliasType.PROVIDER_SYMBOL,
        value=contract.symbol,
        effective_from=observed_at,
    )
    return OptionSecurityMasterContract(
        instrument=instrument,
        provider_alias=alias,
        underlying_instrument_id=f"alpaca:{contract.underlying_asset_id}",
        underlying_symbol=contract.underlying_symbol,
        root_symbol=contract.root_symbol,
        expiration_date=contract.expiration_date,
        strike_price=contract.strike_price,
        contract_type=contract.type,
        exercise_style=contract.style,
        multiplier=contract.size,
        tradable=contract.tradable,
        open_interest=contract.open_interest,
        open_interest_date=contract.open_interest_date,
        close_price=contract.close_price,
        close_price_date=contract.close_price_date,
        observed_at=observed_at,
    )


__all__ = ("project_option_security_master_contract",)

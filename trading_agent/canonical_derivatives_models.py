from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.alpaca_option_chain_models import (
    OptionChainRequest,
    OptionContractType,
)
from trading_agent.alpaca_option_contract_models import (
    OptionContractCatalogRequest,
)
from trading_agent.data_capability_models import DataSourceId

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CanonicalDerivativesStatus(StrEnum):
    BLOCKED = "blocked"
    READY = "ready"


class CanonicalDerivativesReason(StrEnum):
    INDICATIVE_RESEARCH_ONLY = "indicative_research_only"
    OPTIONS_ENTITLEMENT_MISSING = "options_entitlement_missing"
    DERIVATIVE_SURFACE_STALE = "derivative_surface_stale"
    DERIVATIVES_SOURCE_INVALID = "derivatives_source_invalid"
    DERIVATIVES_EVIDENCE_OVER_BROAD = "derivatives_evidence_over_broad"
    CURRENT_QUOTE_NOT_LICENSED = "current_quote_not_licensed"
    CME_SUB_ENTITLEMENT_MISSING = "cme_sub_entitlement_missing"


class CanonicalDerivativeContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument_id: str
    provider_symbol: str
    underlying_symbol: str
    expiration_date: dt.date
    contract_type: OptionContractType
    strike_price: Decimal = Field(ge=0)
    contract_observed_at: dt.datetime
    quote_observed_at: dt.datetime
    bid_price: Decimal = Field(ge=0)
    ask_price: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if (
            not self.instrument_id
            or not self.provider_symbol
            or not _aware(self.contract_observed_at)
            or not _aware(self.quote_observed_at)
            or self.bid_price > self.ask_price
        ):
            raise ValueError("canonical derivative contract is invalid")
        return self


class CanonicalDerivativesAdmissionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_request: OptionContractCatalogRequest
    chain_request: OptionChainRequest
    as_of: dt.datetime
    freshness_seconds: int = Field(default=1_200, ge=1, le=86_400)
    max_contracts: int = Field(default=1_000, ge=1, le=1_000)
    kis_admission_path: Path | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        contract = self.contract_request
        chain = self.chain_request
        if (
            not _aware(self.as_of)
            or contract.underlying_symbol != chain.underlying_symbol
            or contract.expiration_date != chain.expiration_date
            or contract.contract_type is not chain.contract_type
        ):
            raise ValueError("canonical derivatives request is invalid")
        return self


class CanonicalDerivativesEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    status: CanonicalDerivativesStatus
    terminal_reason: CanonicalDerivativesReason
    observed_at: dt.datetime
    source_id: DataSourceId | None = None
    contract_request_id: str | None = None
    contract_run_id: str | None = None
    contract_receipt_ids: tuple[str, ...] = Field(default=(), max_length=8)
    chain_request_id: str | None = None
    chain_run_id: str | None = None
    chain_receipt_ids: tuple[str, ...] = Field(default=(), max_length=8)
    capability_state: Literal["complete", "unavailable"]
    entitlement_state: Literal["research_only", "unavailable"]
    contracts: tuple[CanonicalDerivativeContract, ...] = Field(default=(), max_length=1_000)
    current_authority: Literal[False] = False
    selectable: Literal[False] = False
    network_access: Literal[0] = 0
    provider_mutation: Literal[0] = 0
    account_or_order_mutation: Literal[0] = 0

    @model_validator(mode="after")
    def validate_terminal(self) -> Self:
        ready = (
            self.status is CanonicalDerivativesStatus.READY
            and self.terminal_reason is CanonicalDerivativesReason.INDICATIVE_RESEARCH_ONLY
            and self.source_id is not None
            and self.capability_state == "complete"
            and self.entitlement_state == "research_only"
            and bool(self.contracts)
            and _lineage_complete(self)
        )
        blocked = (
            self.status is CanonicalDerivativesStatus.BLOCKED
            and self.terminal_reason is not CanonicalDerivativesReason.INDICATIVE_RESEARCH_ONLY
            and self.capability_state == "unavailable"
            and self.entitlement_state == "unavailable"
            and not self.contracts
        )
        if not _aware(self.observed_at) or not (ready or blocked):
            raise ValueError("canonical derivatives evidence is invalid")
        return self


def _lineage_complete(value: CanonicalDerivativesEvidence) -> bool:
    identities = (
        value.contract_request_id,
        value.contract_run_id,
        value.chain_request_id,
        value.chain_run_id,
    )
    return (
        all(item is not None and _SHA256.fullmatch(item) for item in identities)
        and bool(value.contract_receipt_ids)
        and bool(value.chain_receipt_ids)
        and all(_SHA256.fullmatch(item) for item in value.contract_receipt_ids)
        and all(_SHA256.fullmatch(item) for item in value.chain_receipt_ids)
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "CanonicalDerivativeContract",
    "CanonicalDerivativesAdmissionRequest",
    "CanonicalDerivativesEvidence",
    "CanonicalDerivativesReason",
    "CanonicalDerivativesStatus",
)

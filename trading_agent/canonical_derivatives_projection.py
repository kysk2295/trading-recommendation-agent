from __future__ import annotations

import datetime as dt
from pathlib import Path

from pydantic import ValidationError

from trading_agent.alpaca_option_chain_capability import (
    AlpacaOptionChainCapabilityError,
    project_alpaca_option_chain_capability,
)
from trading_agent.alpaca_option_chain_models import (
    OptionChainFailure,
    OptionChainRawResponse,
    OptionChainRequest,
    OptionChainStatus,
    OptionContractSnapshot,
    OptionFeed,
)
from trading_agent.alpaca_option_chain_projection import (
    merge_option_chain_page,
    parse_option_chain_page,
)
from trading_agent.alpaca_option_chain_store import (
    AlpacaOptionChainStore,
    AlpacaOptionChainStoreError,
)
from trading_agent.alpaca_option_contract_models import (
    OptionCatalogStatus,
    OptionContractCatalogRequest,
    OptionContractRawResponse,
    OptionSecurityMasterContract,
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
from trading_agent.alpaca_option_contract_store import (
    AlpacaOptionContractStore,
    AlpacaOptionContractStoreError,
)
from trading_agent.canonical_derivatives_models import (
    CanonicalDerivativeContract,
    CanonicalDerivativesAdmissionRequest,
    CanonicalDerivativesEvidence,
    CanonicalDerivativesReason,
    CanonicalDerivativesStatus,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kis_futures_entitlement_admission import (
    KisFuturesAdmissionReason,
    KisFuturesAdmissionStatus,
    KisFuturesEntitlementAdmission,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    read_private_text,
)


def project_canonical_derivatives_evidence(
    contract_store: AlpacaOptionContractStore,
    chain_store: AlpacaOptionChainStore,
    request: CanonicalDerivativesAdmissionRequest,
) -> CanonicalDerivativesEvidence:
    try:
        return _project(contract_store, chain_store, request)
    except _Blocked as blocked:
        return _blocked(request.as_of, blocked.reason)
    except (
        AlpacaOptionChainCapabilityError,
        AlpacaOptionChainStoreError,
        AlpacaOptionContractStoreError,
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        return _blocked(
            request.as_of,
            CanonicalDerivativesReason.DERIVATIVES_SOURCE_INVALID,
        )


def _project(
    contract_store: AlpacaOptionContractStore,
    chain_store: AlpacaOptionChainStore,
    request: CanonicalDerivativesAdmissionRequest,
) -> CanonicalDerivativesEvidence:
    master = contract_store.run(request.contract_request.request_id)
    chain = chain_store.run(request.chain_request.request_id)
    if master is None or chain is None:
        raise _Blocked(CanonicalDerivativesReason.OPTIONS_ENTITLEMENT_MISSING)
    if master.status is not OptionCatalogStatus.SUCCESS or chain.status is not OptionChainStatus.SUCCESS:
        raise _Blocked(CanonicalDerivativesReason.DERIVATIVES_SOURCE_INVALID)
    contract_receipts = contract_store.receipts(master.request.request_id)
    chain_receipts = chain_store.receipts(chain.request.request_id)
    if (
        tuple(item.receipt_id for item in contract_receipts) != master.receipt_ids
        or tuple(item.receipt_id for item in chain_receipts) != chain.receipt_ids
        or _replay_contracts(master.request, master.completed_at, contract_receipts) != master.contracts
        or _replay_snapshots(chain.request, chain_receipts) != chain.snapshots
    ):
        raise _Blocked(CanonicalDerivativesReason.DERIVATIVES_SOURCE_INVALID)
    if len(master.contracts) > request.max_contracts or len(chain.snapshots) > request.max_contracts:
        raise _Blocked(CanonicalDerivativesReason.DERIVATIVES_EVIDENCE_OVER_BROAD)
    if chain.request.feed is OptionFeed.OPRA:
        raise _Blocked(CanonicalDerivativesReason.CURRENT_QUOTE_NOT_LICENSED)
    _require_kis_admission(request.kis_admission_path)
    capability = project_alpaca_option_chain_capability(chain)
    if not capability.complete:
        raise _Blocked(CanonicalDerivativesReason.DERIVATIVES_SOURCE_INVALID)
    contracts = _canonical_contracts(master.contracts, chain.snapshots)
    if not contracts or any(
        item.quote_observed_at > request.as_of
        or request.as_of - item.quote_observed_at > dt.timedelta(seconds=request.freshness_seconds)
        or item.contract_observed_at > request.as_of
        for item in contracts
    ):
        raise _Blocked(CanonicalDerivativesReason.DERIVATIVE_SURFACE_STALE)
    return CanonicalDerivativesEvidence(
        status=CanonicalDerivativesStatus.READY,
        terminal_reason=CanonicalDerivativesReason.INDICATIVE_RESEARCH_ONLY,
        observed_at=request.as_of,
        source_id=capability.capability.source_id,
        contract_request_id=master.request.request_id,
        contract_run_id=master.run_id,
        contract_receipt_ids=master.receipt_ids,
        chain_request_id=chain.request.request_id,
        chain_run_id=chain.run_id,
        chain_receipt_ids=chain.receipt_ids,
        capability_state="complete",
        entitlement_state="research_only",
        contracts=contracts,
    )


def _replay_contracts(
    request: OptionContractCatalogRequest,
    completed_at: dt.datetime,
    receipts: tuple[OptionContractRawResponse, ...],
) -> tuple[OptionSecurityMasterContract, ...]:
    contracts: dict[str, ProviderOptionContract] = {}
    symbols: set[str] = set()
    page_token: str | None = None
    for page_index, receipt in enumerate(receipts):
        if receipt.page_index != page_index or receipt.page_token != page_token:
            raise ValueError
        page = parse_option_contract_page(request, receipt)
        if not isinstance(page, ProviderOptionContractPage):
            raise ValueError
        if merge_option_contract_page(contracts, symbols, page) is not None:
            raise ValueError
        page_token = page.page_token
    if page_token is not None:
        raise ValueError
    return tuple(
        sorted(
            (project_option_security_master_contract(item, completed_at) for item in contracts.values()),
            key=lambda item: item.instrument.value,
        )
    )


def _replay_snapshots(
    request: OptionChainRequest,
    receipts: tuple[OptionChainRawResponse, ...],
) -> tuple[OptionContractSnapshot, ...]:
    snapshots: dict[str, OptionContractSnapshot] = {}
    page_token: str | None = None
    for page_index, receipt in enumerate(receipts):
        if receipt.page_index != page_index or receipt.page_token != page_token:
            raise ValueError
        page = parse_option_chain_page(request, receipt)
        if isinstance(page, OptionChainFailure):
            raise ValueError
        if merge_option_chain_page(snapshots, page) is not None:
            raise ValueError
        page_token = page.next_page_token
    if page_token is not None:
        raise ValueError
    return tuple(snapshots[key] for key in sorted(snapshots))


def _canonical_contracts(
    masters: tuple[OptionSecurityMasterContract, ...],
    snapshots: tuple[OptionContractSnapshot, ...],
) -> tuple[CanonicalDerivativeContract, ...]:
    by_symbol = {item.provider_alias.value: item for item in masters}
    projected: list[CanonicalDerivativeContract] = []
    for snapshot in snapshots:
        master = by_symbol.get(snapshot.symbol)
        quote = snapshot.latest_quote
        if master is None or quote is None:
            raise ValueError
        projected.append(
            CanonicalDerivativeContract(
                instrument_id=master.instrument.value,
                provider_symbol=snapshot.symbol,
                underlying_symbol=snapshot.underlying_symbol,
                expiration_date=snapshot.expiration_date,
                contract_type=snapshot.contract_type,
                strike_price=snapshot.strike_price,
                contract_observed_at=master.observed_at,
                quote_observed_at=quote.timestamp,
                bid_price=quote.bid_price,
                ask_price=quote.ask_price,
            )
        )
    return tuple(projected)


def _require_kis_admission(path: Path | None) -> None:
    if path is None:
        return
    payload = read_private_text(path)
    admission = KisFuturesEntitlementAdmission.model_validate_json(payload)
    if payload != canonical_experiment_ledger_json(admission) + "\n":
        raise ValueError
    if (
        admission.status is KisFuturesAdmissionStatus.BLOCKED
        and admission.reason is KisFuturesAdmissionReason.CME_SUB_ENTITLEMENT_MISSING
    ):
        raise _Blocked(CanonicalDerivativesReason.CME_SUB_ENTITLEMENT_MISSING)
    if admission.status is not KisFuturesAdmissionStatus.READY:
        raise _Blocked(CanonicalDerivativesReason.OPTIONS_ENTITLEMENT_MISSING)


def _blocked(
    observed_at: dt.datetime,
    reason: CanonicalDerivativesReason,
) -> CanonicalDerivativesEvidence:
    return CanonicalDerivativesEvidence(
        status=CanonicalDerivativesStatus.BLOCKED,
        terminal_reason=reason,
        observed_at=observed_at,
        capability_state="unavailable",
        entitlement_state="unavailable",
    )


class _Blocked(Exception):
    def __init__(self, reason: CanonicalDerivativesReason) -> None:
        self.reason = reason


__all__ = ("project_canonical_derivatives_evidence",)

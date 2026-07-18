from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from trading_agent.alpaca_security_master_models import AlpacaSecurityMasterSnapshot
from trading_agent.data_foundation_manifest import DataFoundationManifest
from trading_agent.security_master_models import (
    AssetClass,
    DataMarketDomain,
    InstrumentAlias,
    InstrumentAliasType,
    InstrumentId,
)
from trading_agent.signal_contract_models import OpportunityCandidate, OpportunitySnapshot
from trading_agent.strategy_data_gate import StrategyDataStatus
from trading_agent.us_opportunity_scanner_models import UsOpportunityScannerProjectionError


@dataclass(frozen=True, slots=True)
class ResolvedUsOpportunityCandidate:
    candidate: OpportunityCandidate
    instrument: InstrumentId
    canonical_payload: bytes


def resolve_us_opportunity_candidates(
    opportunity: OpportunitySnapshot,
    foundation: DataFoundationManifest,
    security_master: AlpacaSecurityMasterSnapshot | None,
) -> tuple[ResolvedUsOpportunityCandidate, ...]:
    if (
        type(foundation) is not DataFoundationManifest
        or foundation.evaluated_at > opportunity.observed_at
    ):
        raise UsOpportunityScannerProjectionError
    if security_master is None:
        instruments = foundation.instruments
        aliases = foundation.aliases
        security_master_id = None
    else:
        if (
            type(security_master) is not AlpacaSecurityMasterSnapshot
            or security_master.observed_at > opportunity.observed_at
            or opportunity.observed_at - security_master.observed_at > dt.timedelta(days=3)
            or foundation.evaluate_data_readiness().status is not StrategyDataStatus.READY
            or any(
                capability.source_id.provider == "fixture"
                for capability in foundation.capabilities
            )
        ):
            raise UsOpportunityScannerProjectionError
        instruments = security_master.instruments
        aliases = security_master.aliases
        security_master_id = security_master.snapshot_id
    instruments_by_id = {instrument.value: instrument for instrument in instruments}
    return tuple(
        _resolve_candidate(
            candidate,
            opportunity,
            foundation.manifest_id,
            security_master_id,
            aliases,
            instruments_by_id,
        )
        for candidate in opportunity.candidates
    )


def _resolve_candidate(
    candidate: OpportunityCandidate,
    opportunity: OpportunitySnapshot,
    foundation_id: str,
    security_master_id: str | None,
    aliases: tuple[InstrumentAlias, ...],
    instruments: dict[str, InstrumentId],
) -> ResolvedUsOpportunityCandidate:
    matches = tuple(
        alias
        for alias in aliases
        if alias.value == candidate.symbol
        and alias.alias_type
        in {InstrumentAliasType.SYMBOL, InstrumentAliasType.PROVIDER_SYMBOL}
        and alias.effective_from <= opportunity.observed_at
        and (alias.effective_to is None or opportunity.observed_at < alias.effective_to)
    )
    if len(matches) != 1:
        raise UsOpportunityScannerProjectionError
    instrument = instruments[matches[0].instrument_id]
    if (
        instrument.market_domain is not DataMarketDomain.US_EQUITIES
        or instrument.asset_class not in {AssetClass.EQUITY, AssetClass.ETF}
        or instrument.currency != "USD"
        or instrument.valid_from > opportunity.observed_at
        or (
            instrument.valid_to is not None
            and opportunity.observed_at >= instrument.valid_to
        )
        or candidate.score < 0
    ):
        raise UsOpportunityScannerProjectionError
    payload = {
        "candidate": candidate.model_dump(mode="json"),
        "foundation_id": foundation_id,
        "instrument_id": instrument.value,
    }
    if security_master_id is not None:
        payload["security_master_id"] = security_master_id
    canonical_payload = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return ResolvedUsOpportunityCandidate(candidate, instrument, canonical_payload)


__all__ = (
    "ResolvedUsOpportunityCandidate",
    "resolve_us_opportunity_candidates",
)

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from trading_agent.alpaca_security_master_models import (
    AlpacaSecurityMasterSnapshot,
    build_alpaca_security_master_snapshot,
)
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.security_master_models import (
    AssetClass,
    DataMarketDomain,
    InstrumentAlias,
    InstrumentAliasType,
    InstrumentId,
)
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)
from trading_agent.strategy_data_gate import StrategyDataStatus
from trading_agent.us_broad_scanner_foundation import (
    UsBroadScannerFoundationError,
    build_us_broad_scanner_foundation,
)

OBSERVED_AT = dt.datetime(2026, 7, 20, 13, 30, tzinfo=dt.UTC)


def test_complete_kis_coverage_and_current_master_build_ready_foundation() -> None:
    opportunity = _opportunity()
    security = _security_master()

    first = build_us_broad_scanner_foundation(opportunity, security)
    second = build_us_broad_scanner_foundation(opportunity, security)

    assert second == first
    assert first.strategy_lane == opportunity.strategy_lane
    assert first.instruments == security.instruments
    assert first.aliases == security.aliases
    assert tuple(item.source_id.canonical_id for item in first.capabilities) == (
        "alpaca/assets",
        "kis/us_ranking",
        "nyse/current_halts",
    )
    assert tuple(item.requirement_id for item in first.requirements) == (
        "broad-scanner-assets-current",
        "broad-scanner-halts-current",
        "broad-scanner-ranking-current",
    )
    assert first.evaluate_data_readiness().status is StrategyDataStatus.READY
    assert all(item.source_id.provider != "fixture" for item in first.capabilities)


def test_entitlement_authority_is_stable_across_observation_cycles() -> None:
    first = build_us_broad_scanner_foundation(_opportunity(), _security_master())
    later_at = OBSERVED_AT + dt.timedelta(minutes=1)
    opportunity = OpportunitySnapshot.model_validate(
        {
            **_opportunity().model_dump(mode="python"),
            "opportunity_id": "us-opportunity-foundation-later",
            "observed_at": later_at,
            "valid_until": later_at + dt.timedelta(minutes=1),
            "evidence_refs": (
                EvidenceRef(
                    namespace="manual/qa",
                    record_id="foundation:later",
                    observed_at=later_at,
                ),
            ),
            "source_coverage": tuple(
                item.model_copy(update={"observed_at": later_at}) for item in _opportunity().source_coverage
            ),
        }
    )
    security = _security_master(observed_at=later_at - dt.timedelta(minutes=1))

    later = build_us_broad_scanner_foundation(opportunity, security)

    assert later.entitlements == first.entitlements
    assert tuple(item.assessed_at for item in later.capabilities) == (later_at,) * 3
    assert tuple(item.assessed_at for item in first.capabilities) == (OBSERVED_AT,) * 3
    assert all(item.effective_from < OBSERVED_AT for item in first.entitlements)


@pytest.mark.parametrize("mutation", ("missing", "wrong_producer", "stale_security"))
def test_incomplete_or_noncausal_foundation_input_fails_closed(mutation: str) -> None:
    opportunity = _opportunity()
    security = _security_master()
    if mutation == "missing":
        opportunity = OpportunitySnapshot.model_validate(
            {**opportunity.model_dump(), "source_coverage": opportunity.source_coverage[:-1]}
        )
    elif mutation == "wrong_producer":
        opportunity = OpportunitySnapshot.model_validate(
            {
                **opportunity.model_dump(),
                "producer_strategy_version": "other-v1",
            }
        )
    else:
        security = _security_master(observed_at=OBSERVED_AT - dt.timedelta(days=1, seconds=1))

    with pytest.raises(
        UsBroadScannerFoundationError,
        match="US broad scanner foundation is invalid",
    ):
        _ = build_us_broad_scanner_foundation(opportunity, security)


def _opportunity() -> OpportunitySnapshot:
    coverage = (
        *(
            SourceCoverage(
                source_id=f"kis_{source}_{exchange}",
                observed_at=OBSERVED_AT,
                record_count=1,
                complete=True,
            )
            for source in ("updown", "volume")
            for exchange in ("ams", "nas", "nys")
        ),
        SourceCoverage(
            source_id="nyse_halts",
            observed_at=OBSERVED_AT,
            record_count=0,
            complete=True,
        ),
    )
    return OpportunitySnapshot(
        opportunity_id="us-opportunity-foundation-qa",
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.OPPORTUNITY_MANAGER,
            strategy_id="ranking_momentum",
        ),
        producer_strategy_version="kis-risk-screen-v1",
        observed_at=OBSERVED_AT,
        valid_until=OBSERVED_AT + dt.timedelta(minutes=1),
        candidates=(
            OpportunityCandidate(
                symbol="FIXT",
                rank=1,
                score=Decimal("1"),
                features=(FeatureValue(name="change_pct", value="1"),),
            ),
        ),
        evidence_refs=(
            EvidenceRef(
                namespace="manual/qa",
                record_id="foundation:1",
                observed_at=OBSERVED_AT,
            ),
        ),
        source_coverage=tuple(sorted(coverage, key=lambda item: item.source_id)),
    )


def _security_master(
    *,
    observed_at: dt.datetime = OBSERVED_AT - dt.timedelta(minutes=1),
) -> AlpacaSecurityMasterSnapshot:
    instrument = InstrumentId(
        value="alpaca:asset-fixt",
        market_domain=DataMarketDomain.US_EQUITIES,
        asset_class=AssetClass.EQUITY,
        venue="XNAS",
        currency="USD",
        timezone="America/New_York",
        valid_from=observed_at,
    )
    alias = InstrumentAlias(
        instrument_id=instrument.value,
        namespace="alpaca",
        alias_type=InstrumentAliasType.PROVIDER_SYMBOL,
        value="FIXT",
        effective_from=observed_at,
    )
    return build_alpaca_security_master_snapshot(
        "a" * 64,
        observed_at,
        (instrument,),
        (alias,),
    )

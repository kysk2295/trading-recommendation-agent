from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from trading_agent.alpaca_news_opportunity_evidence import (
    AlpacaNewsEvidenceObservation,
    AlpacaNewsOpportunityEvidenceBundle,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.multi_market_experiment_keys import (
    multi_market_strategy_version_registration_key,
)
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)
from trading_agent.us_news_catalyst_opportunity_models import (
    MAX_CANDIDATES,
    UsNewsCatalystOpportunityProjection,
    UsNewsCatalystProjectionError,
    UsNewsCatalystProjectionStatus,
    opportunity_identity,
    projection_identity,
)
from trading_agent.us_news_catalyst_research_registration import (
    US_NEWS_CATALYST_LANE,
    UsNewsCatalystProjectionAuthorityRequest,
    require_registered_us_news_catalyst_strategy,
)

EVENT_FRESHNESS: Final = dt.timedelta(seconds=300)
OPPORTUNITY_VALIDITY: Final = dt.timedelta(seconds=300)
_FRESHNESS_MICROSECONDS: Final = 300_000_000


@dataclass(frozen=True, slots=True)
class _Rankable:
    symbol: str
    observations: tuple[AlpacaNewsEvidenceObservation, ...]
    latest_provider_updated_at: dt.datetime
    latest_age_microseconds: int


def project_registered_us_news_catalyst_opportunity(
    bundle: AlpacaNewsOpportunityEvidenceBundle,
    ledger: ExperimentLedgerReader,
    request: UsNewsCatalystProjectionAuthorityRequest,
) -> UsNewsCatalystOpportunityProjection:
    if request.projected_at != bundle.assessment.assessed_at:
        raise UsNewsCatalystProjectionError
    registration = require_registered_us_news_catalyst_strategy(ledger, request)
    registration_key = str(multi_market_strategy_version_registration_key(registration))
    ranked = tuple(sorted(_rankables(bundle), key=_ranking_key))[:MAX_CANDIDATES]
    snapshot = _snapshot(bundle, request, ranked) if ranked else None
    status = (
        UsNewsCatalystProjectionStatus.RANKED
        if snapshot is not None
        else UsNewsCatalystProjectionStatus.NO_CANDIDATES
    )
    projection_id = projection_identity(
        bundle.bundle_id,
        registration_key,
        request.strategy_version,
        request.projected_at,
        status,
        len(ranked),
        snapshot,
    )
    return UsNewsCatalystOpportunityProjection(
        projection_id=projection_id,
        evidence_bundle_id=bundle.bundle_id,
        strategy_registration_key=registration_key,
        strategy_version=request.strategy_version,
        projected_at=request.projected_at,
        status=status,
        eligible_symbol_count=len(ranked),
        snapshot=snapshot,
    )


def _rankables(bundle: AlpacaNewsOpportunityEvidenceBundle) -> tuple[_Rankable, ...]:
    values: list[_Rankable] = []
    for snapshot in bundle.snapshots:
        recent = tuple(
            item
            for item in snapshot.observations
            if 0 <= _age_microseconds(bundle.assessment.assessed_at, item.provider_updated_at)
            <= _FRESHNESS_MICROSECONDS
        )
        if not recent:
            continue
        latest = max(item.provider_updated_at for item in recent)
        values.append(
            _Rankable(
                symbol=snapshot.symbol,
                observations=recent,
                latest_provider_updated_at=latest,
                latest_age_microseconds=_age_microseconds(bundle.assessment.assessed_at, latest),
            )
        )
    return tuple(values)


def _ranking_key(item: _Rankable) -> tuple[int, int, str]:
    return (-len(item.observations), item.latest_age_microseconds, item.symbol)


def _snapshot(
    bundle: AlpacaNewsOpportunityEvidenceBundle,
    request: UsNewsCatalystProjectionAuthorityRequest,
    ranked: tuple[_Rankable, ...],
) -> OpportunitySnapshot:
    candidates = tuple(
        OpportunityCandidate(
            symbol=item.symbol,
            rank=rank,
            score=_score(item),
            features=_features(item),
        )
        for rank, item in enumerate(ranked, start=1)
    )
    evidence_refs = _evidence_refs(bundle, ranked)
    opportunity_id = _opportunity_id(bundle, request.strategy_version, ranked)
    return OpportunitySnapshot(
        opportunity_id=opportunity_id,
        strategy_lane=US_NEWS_CATALYST_LANE,
        producer_strategy_version=request.strategy_version,
        observed_at=request.projected_at,
        valid_until=request.projected_at + OPPORTUNITY_VALIDITY,
        candidates=candidates,
        evidence_refs=evidence_refs,
        source_coverage=(
            SourceCoverage(
                source_id="alpaca_news",
                observed_at=request.projected_at,
                record_count=bundle.assessment.accepted_article_count,
                complete=True,
            ),
        ),
    )


def _score(item: _Rankable) -> Decimal:
    freshness = Decimal(
        _FRESHNESS_MICROSECONDS - item.latest_age_microseconds
    ) / Decimal(_FRESHNESS_MICROSECONDS + 1)
    return Decimal(len(item.observations)) + freshness


def _features(item: _Rankable) -> tuple[FeatureValue, ...]:
    values = {
        "latest_provider_age_seconds": _seconds_text(item.latest_age_microseconds),
        "latest_provider_updated_at": item.latest_provider_updated_at.isoformat(),
        "recent_article_count": str(len(item.observations)),
    }
    return tuple(FeatureValue(name=name, value=values[name]) for name in sorted(values))


def _evidence_refs(
    bundle: AlpacaNewsOpportunityEvidenceBundle,
    ranked: tuple[_Rankable, ...],
) -> tuple[EvidenceRef, ...]:
    coverage = EvidenceRef(
        namespace="alpaca/news/coverage",
        record_id=bundle.assessment.assessment_id,
        observed_at=bundle.assessment.assessed_at,
    )
    values = {coverage.canonical_id: coverage}
    for candidate in ranked:
        for observation in candidate.observations:
            evidence = EvidenceRef(
                namespace="alpaca/news/article",
                record_id=observation.observation_id,
                observed_at=observation.received_at,
            )
            values[evidence.canonical_id] = evidence
    return tuple(values[key] for key in sorted(values))


def _opportunity_id(
    bundle: AlpacaNewsOpportunityEvidenceBundle,
    strategy_version: str,
    ranked: tuple[_Rankable, ...],
) -> str:
    return opportunity_identity(
        bundle.bundle_id,
        strategy_version,
        bundle.assessment.assessed_at,
        tuple(item.symbol for item in ranked),
    )


def _age_microseconds(later: dt.datetime, earlier: dt.datetime) -> int:
    delta = later - earlier
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def _seconds_text(microseconds: int) -> str:
    return format((Decimal(microseconds) / Decimal(1_000_000)).normalize(), "f")


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "UsNewsCatalystOpportunityProjection",
    "UsNewsCatalystProjectionError",
    "UsNewsCatalystProjectionStatus",
    "project_registered_us_news_catalyst_opportunity",
)

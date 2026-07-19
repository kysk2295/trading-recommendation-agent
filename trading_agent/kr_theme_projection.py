from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.kis_kr_ranking import KisKrRankingItem, KisKrRankingKind
from trading_agent.kr_theme_lane import KR_THEME_OPPORTUNITY_LANE
from trading_agent.kr_theme_models import (
    KrCatalystCollectionCycle,
    KrCatalystObservation,
    KrCatalystSource,
    KrClassifierKind,
    KrCoverageStatus,
    KrRelatedSymbol,
    KrThemeClassification,
    KrThemeDirection,
)
from trading_agent.kr_theme_store import StoredKrCatalyst
from trading_agent.kr_volume_surge_models import (
    InvalidKrVolumeSurgePayloadError,
    KrVolumeSurgePayloadV2,
    KrVolumeSurgeSymbolV2,
    parse_kr_volume_surge_payload,
)
from trading_agent.kr_volume_surge_models import (
    KrVolumeSurgePayload as KrVolumeSurgePayload,
)
from trading_agent.kr_volume_surge_models import (
    KrVolumeSurgeSymbol as KrVolumeSurgeSymbol,
)
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_KR_SYMBOL = re.compile(r"^[0-9]{6}$")


class InvalidKrThemeProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme opportunity projection 계보가 유효하지 않습니다"


class KrProjectedThemeSymbol(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    trading_value_krw: Decimal
    volume_ratio: Decimal

    @model_validator(mode="after")
    def validate_symbol(self) -> Self:
        if (
            _KR_SYMBOL.fullmatch(self.symbol) is None
            or not _nonnegative_finite(self.trading_value_krw)
            or not _nonnegative_finite(self.volume_ratio)
        ):
            raise ValueError("invalid projected KR theme symbol")
        return self


class KrThemeState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    state_id: str
    collection_cycle_id: str
    theme_name: str
    classifier_version: str
    prompt_version: str
    classification_run_id: str
    first_observed_at: dt.datetime
    latest_observed_at: dt.datetime
    projected_at: dt.datetime
    freshness_seconds: int
    catalyst_count: int
    publisher_count: int
    related_symbols: tuple[KrProjectedThemeSymbol, ...]
    total_trading_value_krw: Decimal
    leader_symbol: str
    classification_ids: tuple[str, ...]
    market_catalyst_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        expected_symbols = tuple(
            sorted(
                self.related_symbols,
                key=lambda item: (-item.trading_value_krw, item.symbol),
            )
        )
        expected_freshness = int((self.projected_at - self.first_observed_at).total_seconds())
        if (
            _SAFE_ID.fullmatch(self.state_id) is None
            or _SAFE_ID.fullmatch(self.collection_cycle_id) is None
            or not _canonical_text(self.theme_name, max_length=128)
            or _SAFE_ID.fullmatch(self.classifier_version) is None
            or _SAFE_ID.fullmatch(self.prompt_version) is None
            or _SAFE_ID.fullmatch(self.classification_run_id) is None
            or not _aware(self.first_observed_at)
            or not _aware(self.latest_observed_at)
            or not _aware(self.projected_at)
            or not self.first_observed_at <= self.latest_observed_at <= self.projected_at
            or self.freshness_seconds != expected_freshness
            or self.freshness_seconds < 0
            or self.catalyst_count != len(self.classification_ids)
            or self.catalyst_count < 1
            or not 0 <= self.publisher_count <= self.catalyst_count
            or not self.related_symbols
            or self.related_symbols != expected_symbols
            or self.total_trading_value_krw
            != sum(
                (item.trading_value_krw for item in self.related_symbols),
                start=Decimal(0),
            )
            or self.leader_symbol != self.related_symbols[0].symbol
            or tuple(item.symbol for item in self.related_symbols)
            != tuple(dict.fromkeys(item.symbol for item in self.related_symbols))
            or self.classification_ids != tuple(sorted(set(self.classification_ids)))
            or not all(_SHA256.fullmatch(item) for item in self.classification_ids)
            or not self.market_catalyst_ids
            or self.market_catalyst_ids != tuple(sorted(set(self.market_catalyst_ids)))
            or not all(_SHA256.fullmatch(item) for item in self.market_catalyst_ids)
        ):
            raise ValueError("invalid KR theme state")
        return self


@dataclass(frozen=True, slots=True)
class KrThemeOpportunityProjection:
    state: KrThemeState
    opportunity: OpportunitySnapshot


@dataclass(frozen=True, slots=True)
class _VolumeMetric:
    symbol: str
    trading_value_krw: Decimal
    volume_ratio: Decimal


@dataclass(frozen=True, slots=True)
class _MetricEvidence:
    metric: _VolumeMetric
    catalyst_id: str
    observed_at: dt.datetime


def project_kr_theme_opportunities(
    cycle: KrCatalystCollectionCycle,
    catalysts: tuple[StoredKrCatalyst, ...],
    observations: tuple[KrCatalystObservation, ...],
    classifications: tuple[KrThemeClassification, ...],
    *,
    classifier_version: str,
    prompt_version: str,
    classification_run_id: str,
    projected_at: dt.datetime,
    validity: dt.timedelta,
    producer_strategy_version: str,
) -> tuple[KrThemeOpportunityProjection, ...]:
    try:
        _validate_projection_request(
            cycle,
            projected_at=projected_at,
            validity=validity,
            classifier_version=classifier_version,
            prompt_version=prompt_version,
            classification_run_id=classification_run_id,
            producer_strategy_version=producer_strategy_version,
        )
        catalyst_by_id, observation_by_id = _exact_cycle_evidence(
            cycle,
            catalysts,
            observations,
            projected_at=projected_at,
        )
        selected = _selected_classifications(
            catalyst_by_id,
            classifications,
            classifier_version=classifier_version,
            prompt_version=prompt_version,
            classification_run_id=classification_run_id,
            projected_at=projected_at,
        )
        metrics = _volume_metrics(
            catalyst_by_id,
            observation_by_id,
            projected_at=projected_at,
        )
        coverage = _source_coverage(cycle)
        groups: dict[str, list[KrThemeClassification]] = {}
        for classification in selected:
            if classification.direction is not KrThemeDirection.POSITIVE:
                continue
            if classification.theme_name is None:
                raise InvalidKrThemeProjectionError
            groups.setdefault(classification.theme_name, []).append(classification)
        return tuple(
            _project_theme(
                cycle,
                theme_name,
                tuple(groups[theme_name]),
                catalyst_by_id,
                metrics,
                coverage,
                classifier_version=classifier_version,
                prompt_version=prompt_version,
                classification_run_id=classification_run_id,
                projected_at=projected_at,
                validity=validity,
                producer_strategy_version=producer_strategy_version,
            )
            for theme_name in sorted(groups)
        )
    except InvalidKrThemeProjectionError:
        raise
    except (ArithmeticError, KeyError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeProjectionError from None


def _validate_projection_request(
    cycle: KrCatalystCollectionCycle,
    *,
    projected_at: dt.datetime,
    validity: dt.timedelta,
    classifier_version: str,
    prompt_version: str,
    classification_run_id: str,
    producer_strategy_version: str,
) -> None:
    if (
        not cycle.complete
        or not _aware(projected_at)
        or cycle.completed_at > projected_at
        or not dt.timedelta(0) < validity <= dt.timedelta(hours=1)
        or _SAFE_ID.fullmatch(classifier_version) is None
        or _SAFE_ID.fullmatch(prompt_version) is None
        or _SAFE_ID.fullmatch(classification_run_id) is None
        or _SAFE_ID.fullmatch(producer_strategy_version) is None
    ):
        raise InvalidKrThemeProjectionError


def _exact_cycle_evidence(
    cycle: KrCatalystCollectionCycle,
    catalysts: tuple[StoredKrCatalyst, ...],
    observations: tuple[KrCatalystObservation, ...],
    *,
    projected_at: dt.datetime,
) -> tuple[dict[str, StoredKrCatalyst], dict[str, KrCatalystObservation]]:
    catalyst_by_id = {item.record.catalyst_id: item for item in catalysts}
    observation_by_id = {item.catalyst_id: item for item in observations}
    if (
        len(catalyst_by_id) != len(catalysts)
        or len(observation_by_id) != len(observations)
        or set(catalyst_by_id) != set(observation_by_id)
        or any(hashlib.sha256(item.raw_payload).hexdigest() != item.record.payload_sha256 for item in catalysts)
        or any(item.collection_cycle_id != cycle.collection_cycle_id for item in observations)
        or any(
            item.observed_at > projected_at
            or not cycle.started_at <= item.observed_at <= cycle.completed_at
            or item.observed_at < catalyst_by_id[item.catalyst_id].record.first_observed_at
            for item in observations
        )
    ):
        raise InvalidKrThemeProjectionError
    actual = {source: sum(item.record.source is source for item in catalysts) for source in KrCatalystSource}
    declared = {item.source: item.record_count for item in cycle.coverage}
    if any(actual[source] != declared[source] for source in KrCatalystSource):
        raise InvalidKrThemeProjectionError
    return catalyst_by_id, observation_by_id


def _selected_classifications(
    catalyst_by_id: dict[str, StoredKrCatalyst],
    classifications: tuple[KrThemeClassification, ...],
    *,
    classifier_version: str,
    prompt_version: str,
    classification_run_id: str,
    projected_at: dt.datetime,
) -> tuple[KrThemeClassification, ...]:
    eligible_ids = tuple(
        sorted(
            catalyst_id
            for catalyst_id, catalyst in catalyst_by_id.items()
            if catalyst.record.source in {KrCatalystSource.NEWS, KrCatalystSource.DART}
        )
    )
    selected: list[KrThemeClassification] = []
    for catalyst_id in eligible_ids:
        rows = tuple(
            item
            for item in classifications
            if item.catalyst_id == catalyst_id
            and item.classifier_kind is KrClassifierKind.KEYWORD
            and item.classifier_version == classifier_version
            and item.prompt_version == prompt_version
            and item.classification_run_id == classification_run_id
        )
        if len(rows) != 1:
            raise InvalidKrThemeProjectionError
        classification = rows[0]
        if (
            classification.classified_at > projected_at
            or classification.classified_at < catalyst_by_id[catalyst_id].record.first_observed_at
        ):
            raise InvalidKrThemeProjectionError
        selected.append(classification)
    return tuple(selected)


def _volume_metrics(
    catalyst_by_id: dict[str, StoredKrCatalyst],
    observation_by_id: dict[str, KrCatalystObservation],
    *,
    projected_at: dt.datetime,
) -> dict[str, _MetricEvidence]:
    metrics: dict[str, _MetricEvidence] = {}
    for catalyst_id, catalyst in catalyst_by_id.items():
        if catalyst.record.source is not KrCatalystSource.VOLUME_SURGE:
            continue
        observation = observation_by_id[catalyst_id]
        try:
            payload = parse_kr_volume_surge_payload(catalyst.raw_payload)
        except InvalidKrVolumeSurgePayloadError:
            raise InvalidKrThemeProjectionError from None
        if (
            catalyst.record.content_type != "application/json"
            or catalyst.record.first_observed_at != observation.observed_at
            or payload.observed_at != observation.observed_at
            or payload.observed_at > projected_at
        ):
            raise InvalidKrThemeProjectionError
        if isinstance(payload, KrVolumeSurgePayloadV2):
            _validate_volume_v2_lineage(
                payload,
                catalyst_by_id,
                observation_by_id,
                collection_cycle_id=observation.collection_cycle_id,
            )
        for metric in payload.symbols:
            if metric.symbol in metrics:
                raise InvalidKrThemeProjectionError
            metrics[metric.symbol] = _MetricEvidence(
                metric=_VolumeMetric(
                    symbol=metric.symbol,
                    trading_value_krw=metric.trading_value_krw,
                    volume_ratio=metric.volume_ratio,
                ),
                catalyst_id=catalyst_id,
                observed_at=observation.observed_at,
            )
    return metrics


def _validate_volume_v2_lineage(
    payload: KrVolumeSurgePayloadV2,
    catalyst_by_id: dict[str, StoredKrCatalyst],
    observation_by_id: dict[str, KrCatalystObservation],
    *,
    collection_cycle_id: str,
) -> None:
    if payload.source_run_id != f"{collection_cycle_id}:kis_ranking":
        raise InvalidKrThemeProjectionError
    for metric in payload.symbols:
        _validate_volume_v2_metric(
            metric,
            payload,
            catalyst_by_id,
            observation_by_id,
        )


def _validate_volume_v2_metric(
    metric: KrVolumeSurgeSymbolV2,
    payload: KrVolumeSurgePayloadV2,
    catalyst_by_id: dict[str, StoredKrCatalyst],
    observation_by_id: dict[str, KrCatalystObservation],
) -> None:
    source = catalyst_by_id.get(metric.source_catalyst_id)
    source_observation = observation_by_id.get(metric.source_catalyst_id)
    if source is None or source_observation is None:
        raise InvalidKrThemeProjectionError
    try:
        item = KisKrRankingItem.model_validate_json(source.raw_payload)
    except ValidationError:
        raise InvalidKrThemeProjectionError from None
    if (
        source.record.source is not KrCatalystSource.KIS_RANKING
        or source.record.content_type != "application/json"
        or source.record.first_observed_at != source_observation.observed_at
        or source_observation.observed_at > payload.source_observed_at
        or item.ranking_kind is not KisKrRankingKind.VOLUME
        or item.symbol != metric.symbol
    ):
        raise InvalidKrThemeProjectionError


def _project_theme(
    cycle: KrCatalystCollectionCycle,
    theme_name: str,
    classifications: tuple[KrThemeClassification, ...],
    catalyst_by_id: dict[str, StoredKrCatalyst],
    metrics: dict[str, _MetricEvidence],
    coverage: tuple[SourceCoverage, ...],
    *,
    classifier_version: str,
    prompt_version: str,
    classification_run_id: str,
    projected_at: dt.datetime,
    validity: dt.timedelta,
    producer_strategy_version: str,
) -> KrThemeOpportunityProjection:
    related: dict[str, KrRelatedSymbol] = {}
    for classification in classifications:
        for item in classification.related_symbols:
            existing = related.get(item.symbol)
            if existing is not None and existing != item:
                raise InvalidKrThemeProjectionError
            related[item.symbol] = item
    if not related or any(symbol not in metrics for symbol in related):
        raise InvalidKrThemeProjectionError

    projected_symbols = tuple(
        sorted(
            (
                KrProjectedThemeSymbol(
                    symbol=symbol,
                    trading_value_krw=metrics[symbol].metric.trading_value_krw,
                    volume_ratio=metrics[symbol].metric.volume_ratio,
                )
                for symbol in related
            ),
            key=lambda item: (-item.trading_value_krw, item.symbol),
        )
    )
    classification_ids = tuple(sorted(item.classification_id for item in classifications))
    market_catalyst_ids = tuple(sorted({metrics[symbol].catalyst_id for symbol in related}))
    catalyst_records = tuple(catalyst_by_id[item.catalyst_id].record for item in classifications)
    first_observed_at = min(item.first_observed_at for item in catalyst_records)
    latest_observed_at = max(item.first_observed_at for item in catalyst_records)
    publishers = {item.publisher_id for item in catalyst_records if item.publisher_id is not None}
    state = KrThemeState(
        state_id=_state_id(
            cycle.collection_cycle_id,
            theme_name,
            classifier_version,
            prompt_version,
            classification_run_id,
            projected_at,
            classification_ids,
            market_catalyst_ids,
        ),
        collection_cycle_id=cycle.collection_cycle_id,
        theme_name=theme_name,
        classifier_version=classifier_version,
        prompt_version=prompt_version,
        classification_run_id=classification_run_id,
        first_observed_at=first_observed_at,
        latest_observed_at=latest_observed_at,
        projected_at=projected_at,
        freshness_seconds=int((projected_at - first_observed_at).total_seconds()),
        catalyst_count=len(classifications),
        publisher_count=len(publishers),
        related_symbols=projected_symbols,
        total_trading_value_krw=sum(
            (item.trading_value_krw for item in projected_symbols),
            start=Decimal(0),
        ),
        leader_symbol=projected_symbols[0].symbol,
        classification_ids=classification_ids,
        market_catalyst_ids=market_catalyst_ids,
    )
    opportunity = OpportunitySnapshot(
        opportunity_id=_opportunity_id(state, producer_strategy_version),
        strategy_lane=KR_THEME_OPPORTUNITY_LANE,
        producer_strategy_version=producer_strategy_version,
        observed_at=projected_at,
        valid_until=projected_at + validity,
        candidates=tuple(
            OpportunityCandidate(
                symbol=item.symbol,
                rank=rank,
                score=item.trading_value_krw,
                features=_candidate_features(state, item),
            )
            for rank, item in enumerate(projected_symbols, start=1)
        ),
        evidence_refs=_evidence_refs(cycle, classifications, related, metrics),
        source_coverage=coverage,
    )
    return KrThemeOpportunityProjection(state=state, opportunity=opportunity)


def _candidate_features(
    state: KrThemeState,
    symbol: KrProjectedThemeSymbol,
) -> tuple[FeatureValue, ...]:
    values = {
        "is_leader": "true" if symbol.symbol == state.leader_symbol else "false",
        "theme_catalyst_count": str(state.catalyst_count),
        "theme_freshness_seconds": str(state.freshness_seconds),
        "theme_name": state.theme_name,
        "theme_publisher_count": str(state.publisher_count),
        "theme_related_symbol_count": str(len(state.related_symbols)),
        "theme_total_trading_value_krw": _decimal_text(state.total_trading_value_krw),
        "trading_value_krw": _decimal_text(symbol.trading_value_krw),
        "volume_ratio": _decimal_text(symbol.volume_ratio),
    }
    return tuple(FeatureValue(name=name, value=values[name]) for name in sorted(values))


def _evidence_refs(
    cycle: KrCatalystCollectionCycle,
    classifications: tuple[KrThemeClassification, ...],
    related: dict[str, KrRelatedSymbol],
    metrics: dict[str, _MetricEvidence],
) -> tuple[EvidenceRef, ...]:
    evidence = [
        EvidenceRef(
            namespace="kr/collection_cycle",
            record_id=cycle.collection_cycle_id,
            observed_at=cycle.completed_at,
        )
    ]
    evidence.extend(
        EvidenceRef(
            namespace="kr/theme_classification",
            record_id=item.classification_id,
            observed_at=item.classified_at,
        )
        for item in classifications
    )
    market_observed = {item.catalyst_id: item.observed_at for symbol, item in metrics.items() if symbol in related}
    evidence.extend(
        EvidenceRef(
            namespace="kr/catalyst/volume_surge",
            record_id=catalyst_id,
            observed_at=market_observed[catalyst_id],
        )
        for catalyst_id in sorted(market_observed)
    )
    unique = {item.canonical_id: item for item in evidence}
    return tuple(unique[key] for key in sorted(unique))


def _source_coverage(
    cycle: KrCatalystCollectionCycle,
) -> tuple[SourceCoverage, ...]:
    if any(item.status is not KrCoverageStatus.SUCCESS for item in cycle.coverage):
        raise InvalidKrThemeProjectionError
    return tuple(
        sorted(
            (
                SourceCoverage(
                    source_id=f"kr_{item.source.value}",
                    observed_at=cycle.completed_at,
                    record_count=item.record_count,
                    complete=True,
                )
                for item in cycle.coverage
            ),
            key=lambda item: item.source_id,
        )
    )


def _state_id(
    cycle_id: str,
    theme_name: str,
    classifier_version: str,
    prompt_version: str,
    run_id: str,
    projected_at: dt.datetime,
    classification_ids: tuple[str, ...],
    market_catalyst_ids: tuple[str, ...],
) -> str:
    digest = _identity_digest(
        cycle_id,
        theme_name,
        classifier_version,
        prompt_version,
        run_id,
        projected_at.isoformat(),
        *classification_ids,
        *market_catalyst_ids,
    )
    return f"kr-theme-state-{digest[:20]}"


def _opportunity_id(
    state: KrThemeState,
    producer_strategy_version: str,
) -> str:
    stamp = state.projected_at.astimezone(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    digest = _identity_digest(
        state.state_id,
        state.theme_name,
        producer_strategy_version,
    )
    return f"kr-theme-opportunity-{stamp}-{digest[:12]}"


def _identity_digest(*parts: str) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _nonnegative_finite(value: Decimal) -> bool:
    return value.is_finite() and value >= Decimal(0)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _canonical_text(value: str, *, max_length: int) -> bool:
    return (
        bool(value)
        and value == value.strip()
        and len(value) <= max_length
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )

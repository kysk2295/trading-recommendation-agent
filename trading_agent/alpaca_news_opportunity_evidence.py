from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections import defaultdict
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_agent.alpaca_news_coverage import require_alpaca_news_coverage_assessment
from trading_agent.alpaca_news_coverage_models import (
    AlpacaNewsCoverageAssessment,
    AlpacaNewsCoverageManifest,
)
from trading_agent.alpaca_news_models import AlpacaNewsRunStatus
from trading_agent.alpaca_news_parser import parse_alpaca_news_page
from trading_agent.alpaca_news_replay import require_alpaca_news_run_projection
from trading_agent.alpaca_news_store import AlpacaNewsStore
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.signal_contract_models import EvidenceRef, SourceCoverage

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
_SOURCE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class AlpacaNewsOpportunityEvidenceError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca news Opportunity evidence is invalid"


class AlpacaNewsEvidenceObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    event_id: str
    receipt_id: str
    symbol: str
    source: str
    provider_created_at: dt.datetime
    provider_updated_at: dt.datetime
    received_at: dt.datetime

    @field_validator("provider_created_at", "provider_updated_at", "received_at")
    @classmethod
    def normalize_time(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC) if _aware(value) else value

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        if (
            _HEX64.fullmatch(self.event_id) is None
            or _HEX64.fullmatch(self.receipt_id) is None
            or _SYMBOL.fullmatch(self.symbol) is None
            or _SOURCE.fullmatch(self.source) is None
            or not _aware(self.provider_created_at)
            or not _aware(self.provider_updated_at)
            or not _aware(self.received_at)
            or not self.provider_created_at <= self.provider_updated_at <= self.received_at
        ):
            raise AlpacaNewsOpportunityEvidenceError
        return self

    @property
    def observation_id(self) -> str:
        return _identity(self)


class AlpacaNewsOpportunityEvidenceSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    manifest_id: str
    assessment_id: str
    universe_id: str
    symbol: str
    observed_at: dt.datetime
    observations: tuple[AlpacaNewsEvidenceObservation, ...] = Field(max_length=400)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=401)
    coverage: SourceCoverage

    @field_validator("observed_at")
    @classmethod
    def normalize_observed_at(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC) if _aware(value) else value

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        observation_ids = tuple(item.observation_id for item in self.observations)
        expected_refs = _evidence_refs(self.assessment_id, self.observed_at, self.observations)
        if (
            _HEX64.fullmatch(self.manifest_id) is None
            or _HEX64.fullmatch(self.assessment_id) is None
            or _SYMBOL.fullmatch(self.symbol) is None
            or not _aware(self.observed_at)
            or observation_ids != tuple(sorted(set(observation_ids)))
            or any(item.symbol != self.symbol or item.received_at > self.observed_at for item in self.observations)
            or self.evidence_refs != expected_refs
            or self.coverage.source_id != "alpaca_news"
            or self.coverage.observed_at != self.observed_at
            or self.coverage.record_count != len(self.observations)
            or not self.coverage.complete
        ):
            raise AlpacaNewsOpportunityEvidenceError
        return self

    @property
    def snapshot_id(self) -> str:
        return _identity(self)


class AlpacaNewsOpportunityEvidenceBundle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    manifest: AlpacaNewsCoverageManifest
    assessment: AlpacaNewsCoverageAssessment
    snapshots: tuple[AlpacaNewsOpportunityEvidenceSnapshot, ...] = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def validate_bundle(self) -> Self:
        symbols = tuple(item.symbol for item in self.snapshots)
        if (
            not self.assessment.complete
            or self.assessment.manifest_id != self.manifest.manifest_id
            or self.assessment.universe_id != self.manifest.universe_id
            or self.assessment.assessed_at != self.manifest.cutoff_at
            or symbols != self.manifest.symbols
            or any(
                item.manifest_id != self.manifest.manifest_id
                or item.assessment_id != self.assessment.assessment_id
                or item.universe_id != self.manifest.universe_id
                or item.observed_at != self.assessment.assessed_at
                for item in self.snapshots
            )
        ):
            raise AlpacaNewsOpportunityEvidenceError
        return self

    @property
    def bundle_id(self) -> str:
        return _identity(self)


def project_alpaca_news_opportunity_evidence(
    manifest: AlpacaNewsCoverageManifest,
    assessment: AlpacaNewsCoverageAssessment,
    store: AlpacaNewsStore,
) -> AlpacaNewsOpportunityEvidenceBundle:
    require_alpaca_news_coverage_assessment(manifest, assessment, store)
    if not assessment.complete:
        raise AlpacaNewsOpportunityEvidenceError
    grouped: defaultdict[str, list[AlpacaNewsEvidenceObservation]] = defaultdict(list)
    for request in manifest.requests:
        run = store.run(request.request_id)
        receipts = store.receipts(request.request_id)
        if run is None or run.status is not AlpacaNewsRunStatus.SUCCESS:
            raise AlpacaNewsOpportunityEvidenceError
        _ = require_alpaca_news_run_projection(run, tuple(item.response for item in receipts))
        for stored in receipts:
            page = parse_alpaca_news_page(request, stored.response)
            for article in page.articles:
                for symbol in sorted(set(request.symbols).intersection(article.symbols)):
                    grouped[symbol].append(
                        AlpacaNewsEvidenceObservation(
                            event_id=article.event_id,
                            receipt_id=stored.response.receipt_id,
                            symbol=symbol,
                            source=article.source,
                            provider_created_at=article.created_at,
                            provider_updated_at=article.updated_at,
                            received_at=stored.response.received_at,
                        )
                    )
    snapshots = tuple(
        _snapshot(manifest, assessment, symbol, tuple(grouped[symbol]))
        for symbol in manifest.symbols
    )
    return AlpacaNewsOpportunityEvidenceBundle(
        manifest=manifest,
        assessment=assessment,
        snapshots=snapshots,
    )


def _snapshot(
    manifest: AlpacaNewsCoverageManifest,
    assessment: AlpacaNewsCoverageAssessment,
    symbol: str,
    observations: tuple[AlpacaNewsEvidenceObservation, ...],
) -> AlpacaNewsOpportunityEvidenceSnapshot:
    ordered = tuple(sorted(observations, key=lambda item: item.observation_id))
    return AlpacaNewsOpportunityEvidenceSnapshot(
        manifest_id=manifest.manifest_id,
        assessment_id=assessment.assessment_id,
        universe_id=manifest.universe_id,
        symbol=symbol,
        observed_at=assessment.assessed_at,
        observations=ordered,
        evidence_refs=_evidence_refs(assessment.assessment_id, assessment.assessed_at, ordered),
        coverage=SourceCoverage(
            source_id="alpaca_news",
            observed_at=assessment.assessed_at,
            record_count=len(ordered),
            complete=True,
        ),
    )


def _evidence_refs(
    assessment_id: str,
    observed_at: dt.datetime,
    observations: tuple[AlpacaNewsEvidenceObservation, ...],
) -> tuple[EvidenceRef, ...]:
    refs = [
        EvidenceRef(
            namespace="alpaca/news/coverage",
            record_id=assessment_id,
            observed_at=observed_at,
        )
    ]
    refs.extend(
        EvidenceRef(
            namespace="alpaca/news/article",
            record_id=item.observation_id,
            observed_at=item.received_at,
        )
        for item in observations
    )
    return tuple(sorted(refs, key=lambda item: item.canonical_id))


def _identity(model: BaseModel) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(model).encode()).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "AlpacaNewsEvidenceObservation",
    "AlpacaNewsOpportunityEvidenceBundle",
    "AlpacaNewsOpportunityEvidenceError",
    "AlpacaNewsOpportunityEvidenceSnapshot",
    "project_alpaca_news_opportunity_evidence",
)

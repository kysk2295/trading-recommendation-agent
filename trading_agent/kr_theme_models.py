from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_FAILURE_CODE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
_KR_SYMBOL = re.compile(r"^[0-9]{6}$")


class KrCatalystSource(StrEnum):
    NEWS = "news"
    DART = "dart"
    KIS_RANKING = "kis_ranking"
    VOLUME_SURGE = "volume_surge"


class KrCoverageStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class KrClassifierKind(StrEnum):
    LLM = "llm"
    KEYWORD = "keyword"


class KrThemeDirection(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    IRRELEVANT = "irrelevant"


class KrThemeRelation(StrEnum):
    DIRECT_BUSINESS = "direct_business"
    EQUITY_INTEREST = "equity_interest"
    SUPPLY_CHAIN = "supply_chain"
    MARKET_RUMOR = "market_rumor"


class KrCatalystRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    source: KrCatalystSource
    source_record_id: str
    publisher_id: str | None = None
    published_at: dt.datetime | None = None
    first_observed_at: dt.datetime
    content_type: str
    payload_sha256: str

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if (
            not _canonical_text(self.source_record_id, max_length=512)
            or (
                self.publisher_id is not None
                and not _canonical_text(self.publisher_id, max_length=128)
            )
            or not _aware(self.first_observed_at)
            or (
                self.published_at is not None
                and (
                    not _aware(self.published_at)
                    or self.published_at > self.first_observed_at
                )
            )
            or _CONTENT_TYPE.fullmatch(self.content_type) is None
            or _SHA256.fullmatch(self.payload_sha256) is None
        ):
            raise ValueError("invalid KR catalyst record")
        return self

    @property
    def catalyst_id(self) -> str:
        return _identity_hash(self.source.value, self.source_record_id)


class KrCatalystObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    collection_cycle_id: str
    catalyst_id: str
    observed_at: dt.datetime

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        if (
            _SAFE_ID.fullmatch(self.collection_cycle_id) is None
            or _SHA256.fullmatch(self.catalyst_id) is None
            or not _aware(self.observed_at)
        ):
            raise ValueError("invalid KR catalyst observation")
        return self


class KrSourceCoverage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    source: KrCatalystSource
    status: KrCoverageStatus
    record_count: int
    failure_code: str | None = None

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        failure_valid = (
            self.failure_code is None
            if self.status is KrCoverageStatus.SUCCESS
            else self.failure_code is not None
            and _FAILURE_CODE.fullmatch(self.failure_code) is not None
        )
        if self.record_count < 0 or not failure_valid:
            raise ValueError("invalid KR source coverage")
        return self


class KrCatalystCollectionCycle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    collection_cycle_id: str
    started_at: dt.datetime
    completed_at: dt.datetime
    coverage: tuple[KrSourceCoverage, ...]

    @model_validator(mode="after")
    def validate_cycle(self) -> Self:
        sources = tuple(item.source for item in self.coverage)
        expected = tuple(sorted(KrCatalystSource, key=lambda item: item.value))
        if (
            _SAFE_ID.fullmatch(self.collection_cycle_id) is None
            or not _aware(self.started_at)
            or not _aware(self.completed_at)
            or self.completed_at < self.started_at
            or sources != expected
        ):
            raise ValueError("invalid KR catalyst collection cycle")
        return self

    @property
    def complete(self) -> bool:
        return all(item.status is KrCoverageStatus.SUCCESS for item in self.coverage)


class KrRelatedSymbol(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    symbol: str
    relation: KrThemeRelation
    rationale: str

    @model_validator(mode="after")
    def validate_symbol(self) -> Self:
        if (
            _KR_SYMBOL.fullmatch(self.symbol) is None
            or not _canonical_text(self.rationale, max_length=500)
        ):
            raise ValueError("invalid KR related symbol")
        return self


class KrThemeClassification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    catalyst_id: str
    classifier_kind: KrClassifierKind
    classifier_version: str
    prompt_version: str
    classification_run_id: str
    classified_at: dt.datetime
    direction: KrThemeDirection
    confidence: Decimal
    evidence_quote: str
    theme_name: str | None
    related_symbols: tuple[KrRelatedSymbol, ...]

    @model_validator(mode="after")
    def validate_classification(self) -> Self:
        symbols = tuple(item.symbol for item in self.related_symbols)
        irrelevant = self.direction is KrThemeDirection.IRRELEVANT
        semantic_shape_valid = (
            self.theme_name is None and not self.related_symbols
            if irrelevant
            else self.theme_name is not None
            and _canonical_text(self.theme_name, max_length=128)
            and bool(self.related_symbols)
        )
        if (
            _SHA256.fullmatch(self.catalyst_id) is None
            or _SAFE_ID.fullmatch(self.classifier_version) is None
            or _SAFE_ID.fullmatch(self.prompt_version) is None
            or _SAFE_ID.fullmatch(self.classification_run_id) is None
            or not _aware(self.classified_at)
            or not self.confidence.is_finite()
            or not Decimal(0) <= self.confidence <= Decimal(1)
            or not _canonical_text(self.evidence_quote, max_length=200)
            or symbols != tuple(sorted(set(symbols)))
            or not semantic_shape_valid
        ):
            raise ValueError("invalid KR theme classification")
        return self

    @property
    def classification_id(self) -> str:
        return _identity_hash(
            self.catalyst_id,
            self.classifier_kind.value,
            self.classifier_version,
            self.prompt_version,
            self.classification_run_id,
        )


def _identity_hash(*parts: str) -> str:
    canonical = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _canonical_text(value: str, *, max_length: int) -> bool:
    return (
        bool(value)
        and value == value.strip()
        and len(value) <= max_length
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )

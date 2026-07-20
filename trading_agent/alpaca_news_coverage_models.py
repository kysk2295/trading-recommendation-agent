from __future__ import annotations

import datetime as dt
import hashlib
import re
from enum import StrEnum
from typing import Literal, Self, assert_never, override

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_agent.alpaca_news_models import AlpacaNewsFailure, AlpacaNewsRequest
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class AlpacaNewsCoverageContractError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca news coverage contract is invalid"


class AlpacaNewsCoverageManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    universe_id: str
    cutoff_at: dt.datetime
    requests: tuple[AlpacaNewsRequest, ...] = Field(min_length=1, max_length=8)

    @field_validator("cutoff_at")
    @classmethod
    def normalize_time(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC) if _aware(value) else value

    @field_validator("requests")
    @classmethod
    def canonical_requests(
        cls,
        value: tuple[AlpacaNewsRequest, ...],
    ) -> tuple[AlpacaNewsRequest, ...]:
        return tuple(sorted(value, key=lambda item: item.request_id))

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        first = self.requests[0]
        request_ids = tuple(item.request_id for item in self.requests)
        collection_ids = tuple(item.collection_id for item in self.requests)
        symbols = tuple(symbol for item in self.requests for symbol in item.symbols)
        same_window = all(
            (item.start_at, item.end_at, item.limit, item.max_pages)
            == (first.start_at, first.end_at, first.limit, first.max_pages)
            for item in self.requests
        )
        if (
            _SAFE_ID.fullmatch(self.universe_id) is None
            or not _aware(self.cutoff_at)
            or first.end_at > self.cutoff_at
            or request_ids != tuple(sorted(set(request_ids)))
            or len(collection_ids) != len(set(collection_ids))
            or len(symbols) != len(set(symbols))
            or not same_window
        ):
            raise AlpacaNewsCoverageContractError
        return self

    @property
    def manifest_id(self) -> str:
        return _identity(self)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(symbol for item in self.requests for symbol in item.symbols))


class AlpacaNewsCoverageSliceStatus(StrEnum):
    FAILED = "failed"
    MISSING = "missing"
    SUCCESS = "success"


class AlpacaNewsCoverageSlice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request_id: str
    status: AlpacaNewsCoverageSliceStatus
    run_id: str | None
    completed_at: dt.datetime | None
    page_count: int = Field(ge=0, le=8)
    article_count: int = Field(ge=0, le=400)
    latest_event_at: dt.datetime | None
    failure_code: AlpacaNewsFailure | None

    @field_validator("completed_at", "latest_event_at")
    @classmethod
    def normalize_optional_time(cls, value: dt.datetime | None) -> dt.datetime | None:
        return value.astimezone(dt.UTC) if value is not None and _aware(value) else value

    @model_validator(mode="after")
    def validate_slice(self) -> Self:
        match self.status:
            case AlpacaNewsCoverageSliceStatus.SUCCESS:
                variant = self.run_id is not None and self.completed_at is not None and self.failure_code is None
            case AlpacaNewsCoverageSliceStatus.FAILED:
                variant = self.run_id is not None and self.completed_at is not None and self.failure_code is not None
            case AlpacaNewsCoverageSliceStatus.MISSING:
                variant = (
                    self.run_id is None
                    and self.completed_at is None
                    and self.page_count == 0
                    and self.article_count == 0
                    and self.latest_event_at is None
                    and self.failure_code is None
                )
            case unreachable:
                assert_never(unreachable)
        if (
            _HEX64.fullmatch(self.request_id) is None
            or not variant
            or (self.run_id is not None and _HEX64.fullmatch(self.run_id) is None)
            or (self.completed_at is not None and not _aware(self.completed_at))
            or (self.latest_event_at is not None and not _aware(self.latest_event_at))
            or (
                self.latest_event_at is not None
                and self.completed_at is not None
                and self.latest_event_at > self.completed_at
            )
        ):
            raise AlpacaNewsCoverageContractError
        return self


class AlpacaNewsCoverageAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    manifest_id: str
    universe_id: str
    assessed_at: dt.datetime
    slices: tuple[AlpacaNewsCoverageSlice, ...] = Field(min_length=1, max_length=8)
    declared_symbol_count: int = Field(ge=1, le=400)
    successful_symbol_count: int = Field(ge=0, le=400)
    completeness_bps: int = Field(ge=0, le=10_000)
    accepted_article_count: int = Field(ge=0, le=3_200)
    latest_event_at: dt.datetime | None

    @field_validator("assessed_at", "latest_event_at")
    @classmethod
    def normalize_assessment_time(cls, value: dt.datetime | None) -> dt.datetime | None:
        return value.astimezone(dt.UTC) if value is not None and _aware(value) else value

    @model_validator(mode="after")
    def validate_assessment(self) -> Self:
        request_ids = tuple(item.request_id for item in self.slices)
        if (
            _HEX64.fullmatch(self.manifest_id) is None
            or _SAFE_ID.fullmatch(self.universe_id) is None
            or not _aware(self.assessed_at)
            or request_ids != tuple(sorted(set(request_ids)))
            or self.successful_symbol_count > self.declared_symbol_count
            or self.completeness_bps
            != self.successful_symbol_count * 10_000 // self.declared_symbol_count
            or (self.accepted_article_count == 0) != (self.latest_event_at is None)
            or (
                self.latest_event_at is not None
                and (not _aware(self.latest_event_at) or self.latest_event_at > self.assessed_at)
            )
        ):
            raise AlpacaNewsCoverageContractError
        return self

    @property
    def complete(self) -> bool:
        return self.successful_symbol_count == self.declared_symbol_count

    @property
    def assessment_id(self) -> str:
        return _identity(self)


class AlpacaNewsCoverageArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    manifest: AlpacaNewsCoverageManifest
    assessment: AlpacaNewsCoverageAssessment

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        expected_request_ids = tuple(item.request_id for item in self.manifest.requests)
        actual_request_ids = tuple(item.request_id for item in self.assessment.slices)
        if actual_request_ids != expected_request_ids:
            raise AlpacaNewsCoverageContractError
        request_by_id = {item.request_id: item for item in self.manifest.requests}
        successful_symbols = sum(
            len(request_by_id[item.request_id].symbols)
            for item in self.assessment.slices
            if item.status is AlpacaNewsCoverageSliceStatus.SUCCESS
        )
        accepted_articles = sum(
            item.article_count
            for item in self.assessment.slices
            if item.status is AlpacaNewsCoverageSliceStatus.SUCCESS
        )
        latest = max(
            (
                item.latest_event_at
                for item in self.assessment.slices
                if item.status is AlpacaNewsCoverageSliceStatus.SUCCESS
                and item.latest_event_at is not None
            ),
            default=None,
        )
        if (
            self.assessment.manifest_id != self.manifest.manifest_id
            or self.assessment.universe_id != self.manifest.universe_id
            or self.assessment.assessed_at != self.manifest.cutoff_at
            or self.assessment.declared_symbol_count != len(self.manifest.symbols)
            or self.assessment.successful_symbol_count != successful_symbols
            or self.assessment.accepted_article_count != accepted_articles
            or self.assessment.latest_event_at != latest
        ):
            raise AlpacaNewsCoverageContractError
        return self

    @property
    def artifact_id(self) -> str:
        return _identity(self)


def _identity(model: BaseModel) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(model).encode()).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "AlpacaNewsCoverageArtifact",
    "AlpacaNewsCoverageAssessment",
    "AlpacaNewsCoverageContractError",
    "AlpacaNewsCoverageManifest",
    "AlpacaNewsCoverageSlice",
    "AlpacaNewsCoverageSliceStatus",
)

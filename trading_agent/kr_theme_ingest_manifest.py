from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.kr_theme_models import (
    KrCatalystCollectionCycle,
    KrCatalystObservation,
    KrCatalystRecord,
    KrCatalystSource,
)


class KrThemeManifestError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme ingest manifest 또는 raw payload가 유효하지 않습니다"


class KrCatalystManifestItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    source: KrCatalystSource
    source_record_id: str
    publisher_id: str | None = None
    published_at: dt.datetime | None = None
    observed_at: dt.datetime
    content_type: str
    payload_path: str

    @model_validator(mode="after")
    def validate_item(self) -> Self:
        path = Path(self.payload_path)
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {".", ".."} for part in path.parts)
            or not _aware(self.observed_at)
            or (self.published_at is not None and not _aware(self.published_at))
        ):
            raise ValueError("invalid KR catalyst manifest item")
        return self


class KrThemeIngestManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    cycle: KrCatalystCollectionCycle
    catalysts: tuple[KrCatalystManifestItem, ...]

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        identities = tuple(
            (item.source.value, item.source_record_id)
            for item in self.catalysts
        )
        payload_paths = tuple(item.payload_path for item in self.catalysts)
        actual_counts = {
            source: sum(item.source is source for item in self.catalysts)
            for source in KrCatalystSource
        }
        declared_counts = {
            item.source: item.record_count
            for item in self.cycle.coverage
        }
        if (
            identities != tuple(sorted(set(identities)))
            or len(payload_paths) != len(set(payload_paths))
            or actual_counts != declared_counts
            or any(
                not self.cycle.started_at <= item.observed_at <= self.cycle.completed_at
                for item in self.catalysts
            )
        ):
            raise ValueError("invalid KR theme ingest manifest")
        return self


@dataclass(frozen=True, slots=True)
class LoadedKrCatalyst:
    record: KrCatalystRecord
    observation: KrCatalystObservation
    raw_payload: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class LoadedKrThemeIngest:
    cycle: KrCatalystCollectionCycle
    catalysts: tuple[LoadedKrCatalyst, ...]


def load_kr_theme_ingest_manifest(path: Path) -> LoadedKrThemeIngest:
    try:
        manifest_path = path.resolve(strict=True)
        if not manifest_path.is_file():
            raise OSError
        manifest = KrThemeIngestManifest.model_validate_json(
            manifest_path.read_bytes()
        )
        base = manifest_path.parent
        catalysts = tuple(
            _load_item(base, manifest.cycle.collection_cycle_id, item)
            for item in manifest.catalysts
        )
        return LoadedKrThemeIngest(manifest.cycle, catalysts)
    except (OSError, ValidationError, ValueError) as error:
        if isinstance(error, KrThemeManifestError):
            raise
        raise KrThemeManifestError from error


def _load_item(
    base: Path,
    cycle_id: str,
    item: KrCatalystManifestItem,
) -> LoadedKrCatalyst:
    payload_path = (base / item.payload_path).resolve(strict=True)
    if not payload_path.is_relative_to(base) or not payload_path.is_file():
        raise KrThemeManifestError
    payload = payload_path.read_bytes()
    if not payload:
        raise KrThemeManifestError
    record = KrCatalystRecord(
        source=item.source,
        source_record_id=item.source_record_id,
        publisher_id=item.publisher_id,
        published_at=item.published_at,
        first_observed_at=item.observed_at,
        content_type=item.content_type,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )
    observation = KrCatalystObservation(
        collection_cycle_id=cycle_id,
        catalyst_id=record.catalyst_id,
        observed_at=item.observed_at,
    )
    return LoadedKrCatalyst(record, observation, payload)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None

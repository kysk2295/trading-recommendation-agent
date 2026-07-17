from __future__ import annotations

import datetime as dt
import re
from itertools import pairwise
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.canonical_event_models import (
    CanonicalEntityType,
    CanonicalEventEnvelope,
)
from trading_agent.data_capability_models import (
    DataCapability,
    DataEntitlement,
    StrategyDataRequirement,
)
from trading_agent.research_identity_models import StrategyLaneRef
from trading_agent.security_master_models import (
    CorporateAction,
    InstrumentAlias,
    InstrumentId,
)
from trading_agent.strategy_data_gate import StrategyDataDecision, evaluate_strategy_data

_MANIFEST_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_MAX_MANIFEST_BYTES = 1_048_576


class InvalidDataFoundationManifestError(ValueError):
    @override
    def __str__(self) -> str:
        return "data foundation manifest 계약이 유효하지 않습니다"


class DataFoundationManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    manifest_id: str
    registered_at: dt.datetime
    evaluated_at: dt.datetime
    strategy_lane: StrategyLaneRef
    capabilities: tuple[DataCapability, ...]
    entitlements: tuple[DataEntitlement, ...]
    requirements: tuple[StrategyDataRequirement, ...]
    instruments: tuple[InstrumentId, ...] = ()
    aliases: tuple[InstrumentAlias, ...] = ()
    corporate_actions: tuple[CorporateAction, ...] = ()
    events: tuple[CanonicalEventEnvelope, ...] = ()

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        capability_ids = tuple(capability.source_id.canonical_id for capability in self.capabilities)
        entitlement_source_ids = tuple(entitlement.source_id.canonical_id for entitlement in self.entitlements)
        entitlement_ids = tuple(entitlement.entitlement_id for entitlement in self.entitlements)
        requirement_ids = tuple(requirement.requirement_id for requirement in self.requirements)
        instrument_ids = tuple(instrument.value for instrument in self.instruments)
        alias_keys = tuple(alias.canonical_key for alias in self.aliases)
        action_ids = tuple(action.action_id for action in self.corporate_actions)
        event_ids = tuple(event.event_id for event in self.events)
        declared_sources = set(capability_ids)
        declared_instruments = set(instrument_ids)
        capability_by_source = {
            capability.source_id.canonical_id: capability
            for capability in self.capabilities
        }
        instrument_by_id = {instrument.value: instrument for instrument in self.instruments}
        if (
            _MANIFEST_ID.fullmatch(self.manifest_id) is None
            or not _aware(self.registered_at)
            or not _aware(self.evaluated_at)
            or self.registered_at > self.evaluated_at
            or not self.capabilities
            or capability_ids != tuple(sorted(set(capability_ids)))
            or entitlement_source_ids != tuple(sorted(set(entitlement_source_ids)))
            or entitlement_source_ids != capability_ids
            or len(entitlement_ids) != len(set(entitlement_ids))
            or not self.requirements
            or requirement_ids != tuple(sorted(set(requirement_ids)))
            or any(requirement.strategy_lane != self.strategy_lane for requirement in self.requirements)
            or any(
                source.canonical_id not in declared_sources
                for requirement in self.requirements
                for source in requirement.declared_source_ids
            )
            or instrument_ids != tuple(sorted(set(instrument_ids)))
            or alias_keys != tuple(sorted(set(alias_keys)))
            or action_ids != tuple(sorted(set(action_ids)))
            or event_ids != tuple(sorted(set(event_ids)))
            or not _aliases_valid(self.aliases, instrument_by_id)
            or any(
                action.instrument_id not in declared_instruments
                or (
                    action.successor_instrument_id is not None
                    and action.successor_instrument_id not in declared_instruments
                )
                for action in self.corporate_actions
            )
            or any(
                not _event_valid(
                    event,
                    capability_by_source,
                    declared_instruments,
                    self.evaluated_at,
                )
                for event in self.events
            )
        ):
            raise ValueError("invalid data foundation manifest")
        return self

    def evaluate_data_readiness(self) -> StrategyDataDecision:
        return evaluate_strategy_data(
            self.requirements,
            self.capabilities,
            self.entitlements,
            evaluated_at=self.evaluated_at,
        )


def load_data_foundation_manifest(path: Path) -> DataFoundationManifest:
    try:
        manifest_path = path.resolve(strict=True)
        if not manifest_path.is_file() or manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise OSError
        return DataFoundationManifest.model_validate_json(manifest_path.read_bytes())
    except (OSError, UnicodeError, ValidationError, ValueError):
        raise InvalidDataFoundationManifestError from None


def _aliases_valid(
    aliases: tuple[InstrumentAlias, ...],
    instruments: dict[str, InstrumentId],
) -> bool:
    for alias in aliases:
        instrument = instruments.get(alias.instrument_id)
        if instrument is None or alias.effective_from < instrument.valid_from:
            return False
        if instrument.valid_to is not None and (
            alias.effective_to is None or alias.effective_to > instrument.valid_to
        ):
            return False
    groups: dict[tuple[str, str, str], list[InstrumentAlias]] = {}
    for alias in aliases:
        key = (alias.namespace, alias.alias_type.value, alias.value)
        groups.setdefault(key, []).append(alias)
    for group in groups.values():
        ordered = sorted(group, key=lambda alias: alias.effective_from)
        for previous, current in pairwise(ordered):
            if previous.effective_to is None or current.effective_from < previous.effective_to:
                return False
    return True


def _event_valid(
    event: CanonicalEventEnvelope,
    capabilities: dict[str, DataCapability],
    instruments: set[str],
    evaluated_at: dt.datetime,
) -> bool:
    capability = capabilities.get(event.source_id.canonical_id)
    return (
        capability is not None
        and event.event_type in capability.event_types
        and event.normalized_at <= evaluated_at
        and all(
            entity.entity_type is not CanonicalEntityType.INSTRUMENT
            or entity.entity_id in instruments
            for entity in event.entity_refs
        )
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "DataFoundationManifest",
    "InvalidDataFoundationManifestError",
    "load_data_foundation_manifest",
)

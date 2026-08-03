from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Final, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.private_immutable_file import read_private_text
from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1
from trading_agent.research_agent_source_adapters_research import (
    DerivativesSourceAdapter,
    ResearchSourcePaths,
    SwingSourceAdapter,
    SystematicSourceAdapter,
)
from trading_agent.research_agent_source_common import (
    InvalidResearchAgentSourceError,
    require_source_boundary,
)

ResearchAgentFamily = Literal["swing_trading", "systematic_quant", "derivatives_research"]
InspectionStatus = Literal["ready", "blocked", "invalid"]
_FAMILY_EVIDENCE_CAP: Final = 96
_OUTPUT_CAP: Final = 8


class ResearchInspectionSourcePaths(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    outputs_root: Path
    swing_shadow_database: Path
    swing_review_database: Path
    experiment_ledger: Path
    lane_review_database: Path

    @model_validator(mode="after")
    def require_safe_boundaries(self) -> Self:
        for path in (
            self.outputs_root,
            self.swing_shadow_database,
            self.swing_review_database,
            self.experiment_ledger,
            self.lane_review_database,
        ):
            require_source_boundary(path)
        return self


class _ResearchInspectionServiceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    source_paths: ResearchInspectionSourcePaths


class ResearchFamilySourceInspection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    agent_family_id: ResearchAgentFamily
    status: InspectionStatus
    evidence_count: int = Field(ge=0, le=_FAMILY_EVIDENCE_CAP)
    truncated: bool = False
    source_keys: tuple[str, ...] = Field(max_length=_OUTPUT_CAP)
    provenance_sha256: tuple[str, ...] = Field(max_length=_OUTPUT_CAP)


class ResearchSourceInspection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: InspectionStatus
    inspected_at: dt.datetime
    families: tuple[
        ResearchFamilySourceInspection,
        ResearchFamilySourceInspection,
        ResearchFamilySourceInspection,
    ]
    provider_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    heavy_processes: Literal[0] = 0
    broker_mutation: Literal[0] = 0


class ResearchInspectionAdapter(Protocol):
    def collect(
        self,
        paths: ResearchSourcePaths,
        now: dt.datetime,
    ) -> tuple[ResearchAgentEvidenceV1, ...]: ...


def inspect_research_sources(
    paths: ResearchSourcePaths,
    now: dt.datetime,
) -> ResearchSourceInspection:
    if now.tzinfo is None or now.utcoffset() is None:
        raise InvalidResearchAgentSourceError(reason="collection_time_invalid")
    families = (
        _inspect_family("swing_trading", SwingSourceAdapter(), paths, now),
        _inspect_family("systematic_quant", SystematicSourceAdapter(), paths, now),
        _inspect_family("derivatives_research", DerivativesSourceAdapter(), paths, now),
    )
    statuses = tuple(family.status for family in families)
    status: InspectionStatus = "invalid" if "invalid" in statuses else "blocked" if "blocked" in statuses else "ready"
    return ResearchSourceInspection(status=status, inspected_at=now, families=families)


def load_research_inspection_source_paths(config_path: Path) -> ResearchInspectionSourcePaths:
    payload = read_private_text(config_path.expanduser().absolute())
    return _ResearchInspectionServiceConfig.model_validate_json(payload).source_paths


def _inspect_family(
    family: ResearchAgentFamily,
    adapter: ResearchInspectionAdapter,
    paths: ResearchSourcePaths,
    now: dt.datetime,
) -> ResearchFamilySourceInspection:
    try:
        evidence = adapter.collect(paths, now)
    except InvalidResearchAgentSourceError:
        return ResearchFamilySourceInspection(
            agent_family_id=family,
            status="invalid",
            evidence_count=0,
            truncated=False,
            source_keys=(),
            provenance_sha256=(),
        )
    if any(item.agent_family_id != family for item in evidence):
        return ResearchFamilySourceInspection(
            agent_family_id=family,
            status="invalid",
            evidence_count=0,
            truncated=False,
            source_keys=(),
            provenance_sha256=(),
        )
    ordered = tuple(sorted(evidence, key=lambda item: (item.available_at, item.source_key)))
    if len(ordered) > _FAMILY_EVIDENCE_CAP:
        return ResearchFamilySourceInspection(
            agent_family_id=family,
            status="invalid",
            evidence_count=0,
            truncated=False,
            source_keys=(),
            provenance_sha256=(),
        )
    source_keys = tuple(item.source_key for item in ordered[:_OUTPUT_CAP])
    provenance = tuple(sorted({digest for item in ordered for digest in item.evidence_refs}))[:_OUTPUT_CAP]
    status: InspectionStatus = "blocked" if any(".blocked." in item.source_key for item in ordered) else "ready"
    return ResearchFamilySourceInspection(
        agent_family_id=family,
        status=status,
        evidence_count=len(ordered),
        truncated=len(ordered) > _OUTPUT_CAP,
        source_keys=source_keys,
        provenance_sha256=provenance,
    )


__all__ = (
    "ResearchFamilySourceInspection",
    "ResearchInspectionSourcePaths",
    "ResearchSourceInspection",
    "inspect_research_sources",
    "load_research_inspection_source_paths",
)

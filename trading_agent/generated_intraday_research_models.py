from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Self, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class InvalidGeneratedIntradayManifestError(ValueError):
    @override
    def __str__(self) -> str:
        return "generated intraday research manifest invalid"


class GeneratedStrategySelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["generated_python"] = "generated_python"
    artifact_id: str
    hypothesis_id: str
    strategy_version: str
    queue_card_key: str
    data_foundation_sha256: str
    runtime_fingerprint: str
    sandbox_profile_version: Literal["generated_strategy_sandbox_v1"]

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        if (
            _HEX64.fullmatch(self.artifact_id) is None
            or _IDENTIFIER.fullmatch(self.hypothesis_id) is None
            or self.strategy_version != f"generated-python:{self.artifact_id}"
            or any(
                _HEX64.fullmatch(value) is None
                for value in (
                    self.queue_card_key,
                    self.data_foundation_sha256,
                    self.runtime_fingerprint,
                )
            )
        ):
            raise InvalidGeneratedIntradayManifestError
        return self


class GeneratedIntradayResearchManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    family: Literal["generated_python_intraday_v1"] = "generated_python_intraday_v1"
    hypotheses: tuple[GeneratedStrategySelection, ...]
    source_queue_snapshot_id: str
    input_sha256: str
    registered_at: AwareDatetime
    evaluator_version: Literal["generated_intraday_walk_forward_v1"] = (
        "generated_intraday_walk_forward_v1"
    )
    minimum_training_sessions: int = Field(ge=0, le=20)
    max_bars: int = Field(ge=1, le=100_000)
    max_sessions: int = Field(ge=1, le=60)
    per_side_fee_bps: int = Field(ge=0, le=100)
    per_side_slippage_bps: int = Field(ge=0, le=100)
    bootstrap_samples: int = Field(ge=100, le=5_000)
    rss_limit_gib: float = Field(gt=0.0, le=9.5)

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        artifact_ids = tuple(item.artifact_id for item in self.hypotheses)
        hypothesis_ids = tuple(item.hypothesis_id for item in self.hypotheses)
        strategy_versions = tuple(item.strategy_version for item in self.hypotheses)
        total_cost = self.per_side_fee_bps + self.per_side_slippage_bps
        if (
            not self.hypotheses
            or len(self.hypotheses) > 3
            or len(set(artifact_ids)) != len(artifact_ids)
            or len(set(hypothesis_ids)) != len(hypothesis_ids)
            or len(set(strategy_versions)) != len(strategy_versions)
            or _HEX64.fullmatch(self.source_queue_snapshot_id) is None
            or _HEX64.fullmatch(self.input_sha256) is None
            or not 20 <= total_cost <= 100
            or self.minimum_training_sessions >= self.max_sessions
        ):
            raise InvalidGeneratedIntradayManifestError
        return self

    @property
    def per_side_total_cost_bps(self) -> int:
        return self.per_side_fee_bps + self.per_side_slippage_bps


def load_generated_intraday_research_manifest(path: Path) -> GeneratedIntradayResearchManifest:
    try:
        return GeneratedIntradayResearchManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError, ValueError):
        raise InvalidGeneratedIntradayManifestError from None

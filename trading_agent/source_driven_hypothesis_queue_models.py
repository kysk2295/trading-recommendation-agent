from __future__ import annotations

import datetime as dt
import hashlib
import re
from enum import StrEnum
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_models import ResearchSourceKind
from trading_agent.lane_identity_models import LaneId

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
POLICY_VERSION = "source_driven_hypothesis_queue_v1"


class InvalidSourceDrivenHypothesisQueueError(ValueError):
    @override
    def __str__(self) -> str:
        return "source-driven hypothesis queue evidence is invalid"


class HypothesisQueueRoute(StrEnum):
    EVIDENCE_REVIEW = "evidence_review"
    STRATEGY_DESIGN = "strategy_design"
    HISTORICAL_REPLAY = "historical_replay"
    ACTIVE_RESEARCH = "active_research"
    INDEPENDENT_REVIEW = "independent_review"
    RECOVERY = "recovery"


class SourceDrivenHypothesisQueueItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    card_key: str
    hypothesis_id: str
    lane_id: LaneId
    registered_at: dt.datetime
    hypothesis: str
    falsification_rule: str
    economic_mechanism: str
    counterfactual_baseline: str
    source_keys: tuple[str, ...]
    source_kinds: tuple[ResearchSourceKind, ...]
    strategy_versions: tuple[str, ...]
    historical_trial_ids: tuple[str, ...]
    route: HypothesisQueueRoute

    @model_validator(mode="after")
    def validate_item(self) -> Self:
        if (
            _HEX64.fullmatch(self.card_key) is None
            or not self.hypothesis_id
            or not _aware(self.registered_at)
            or not all(
                value and value == value.strip()
                for value in (
                    self.hypothesis,
                    self.falsification_rule,
                    self.economic_mechanism,
                    self.counterfactual_baseline,
                )
            )
            or not _canonical_hashes(self.source_keys)
            or not self.source_kinds
            or self.source_kinds != tuple(sorted(set(self.source_kinds), key=str))
            or self.strategy_versions != tuple(sorted(set(self.strategy_versions)))
            or self.historical_trial_ids != tuple(sorted(set(self.historical_trial_ids)))
        ):
            raise InvalidSourceDrivenHypothesisQueueError
        return self


class SourceDrivenHypothesisQueueSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    policy_version: Literal["source_driven_hypothesis_queue_v1"] = POLICY_VERSION
    as_of: dt.datetime
    items: tuple[SourceDrivenHypothesisQueueItem, ...]
    lifecycle_authority: Literal[False] = False
    allocation_authority: Literal[False] = False
    order_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        if not _aware(self.as_of) or not self.items:
            raise InvalidSourceDrivenHypothesisQueueError
        return self


class SourceDrivenHypothesisQueueArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    snapshot_id: str
    snapshot: SourceDrivenHypothesisQueueSnapshot

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        expected = hashlib.sha256(canonical_experiment_ledger_json(self.snapshot).encode()).hexdigest()
        if self.snapshot_id != expected:
            raise InvalidSourceDrivenHypothesisQueueError
        return self


def _canonical_hashes(values: tuple[str, ...]) -> bool:
    return bool(values) and values == tuple(sorted(set(values))) and all(_HEX64.fullmatch(value) for value in values)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "POLICY_VERSION",
    "HypothesisQueueRoute",
    "InvalidSourceDrivenHypothesisQueueError",
    "SourceDrivenHypothesisQueueArtifact",
    "SourceDrivenHypothesisQueueItem",
    "SourceDrivenHypothesisQueueSnapshot",
)

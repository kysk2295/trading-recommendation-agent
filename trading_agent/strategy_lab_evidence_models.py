from __future__ import annotations

import datetime as dt
import math
from itertools import pairwise
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.strategy_lab_errors import StrategyLabModelError
from trading_agent.strategy_lab_types import STRATEGY_LAB_IDS, EvidenceMode, StrategyLabId


class LabObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    signal: float
    forward_return: float
    baseline_return: float

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        values = (self.signal, self.forward_return, self.baseline_return)
        if not all(math.isfinite(value) for value in values):
            raise StrategyLabModelError("strategy lab observations must be finite")
        return self


class LabEvidenceBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    lab_id: StrategyLabId
    dataset_id: str = Field(min_length=1)
    period_start: dt.date
    period_end: dt.date
    available_at: dt.datetime
    source_ref: str = Field(min_length=1)
    evidence_mode: EvidenceMode
    feature_name: str = Field(min_length=1)
    target_name: str = Field(min_length=1)
    cost_bps: int = Field(ge=0, le=1_000)
    observations: tuple[LabObservation, ...] = Field(min_length=1, max_length=100_000)

    @model_validator(mode="after")
    def validate_batch(self) -> Self:
        if (
            self.period_end < self.period_start
            or self.available_at.tzinfo is None
            or self.available_at.date() < self.period_end
        ):
            raise StrategyLabModelError("invalid strategy lab evidence batch")
        return self


class StrategyLabEvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    batches: tuple[LabEvidenceBatch, ...]

    @model_validator(mode="after")
    def validate_bundle(self) -> Self:
        batches_by_lab = {
            lab_id: tuple(batch for batch in self.batches if batch.lab_id is lab_id) for lab_id in STRATEGY_LAB_IDS
        }
        if any(not batches_by_lab[lab_id] for lab_id in STRATEGY_LAB_IDS):
            raise StrategyLabModelError("evidence bundle requires all strategy labs")
        if {batch.lab_id for batch in self.batches} != set(STRATEGY_LAB_IDS):
            raise StrategyLabModelError("evidence bundle has unknown strategy lab")
        for lab_id in STRATEGY_LAB_IDS:
            ordered = tuple(sorted(batches_by_lab[lab_id], key=lambda batch: batch.period_start))
            dataset_ids = tuple(batch.dataset_id for batch in ordered)
            if dataset_ids != tuple(dict.fromkeys(dataset_ids)):
                raise StrategyLabModelError("strategy lab dataset identifiers must be unique")
            if any(later.period_start <= earlier.period_end for earlier, later in pairwise(ordered)):
                raise StrategyLabModelError("strategy lab evidence batches must not overlap")
        return self

    def batches_for(self, lab_id: StrategyLabId) -> tuple[LabEvidenceBatch, ...]:
        selected = (batch for batch in self.batches if batch.lab_id is lab_id)
        return tuple(sorted(selected, key=lambda batch: batch.period_start))

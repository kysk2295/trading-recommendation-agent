from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.intraday_research_dataset_catalog_models import (
    MAX_INTRADAY_RESEARCH_CANDIDATE_SESSIONS,
)
from trading_agent.intraday_research_input_binding_models import (
    IntradayResearchStrategyBinding,
)

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX40_OR_64 = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_RUN_KEY = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class IntradayActualResearchPlanError(ValueError):
    @override
    def __str__(self) -> str:
        return "intraday actual research run plan is invalid"


class IntradayActualResearchPlanPaths(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_root: Path
    binding_root: Path
    entitlement_contract: Path
    lane_registry: Path
    experiment_ledger: Path
    artifact_root: Path
    review_root: Path

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        values = tuple(self.__dict__.values())
        if not all(isinstance(path, Path) and path.is_absolute() for path in values):
            raise IntradayActualResearchPlanError
        return self


class IntradayActualResearchRunSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    run_key: str
    session_dirs: tuple[Path, ...]
    required_session_dates: tuple[dt.date, ...]
    strategy_bindings: tuple[IntradayResearchStrategyBinding, ...]
    dataset_producer_commit_sha: str
    code_version: str
    registered_at: dt.datetime
    minimum_clean_sessions: int
    minimum_training_sessions: int
    max_sessions: int
    max_bars: int
    per_side_fee_bps: int
    per_side_slippage_bps: int
    bootstrap_samples: int
    rss_limit_gib: float
    required_outcome_trace_schema_version: Literal[2] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    paths: IntradayActualResearchPlanPaths

    @model_validator(mode="after")
    def validate_spec(self) -> Self:
        bindings = self.strategy_bindings
        if (
            _RUN_KEY.fullmatch(self.run_key) is None
            or not self.session_dirs
            or len(self.session_dirs) > MAX_INTRADAY_RESEARCH_CANDIDATE_SESSIONS
            or len(set(self.session_dirs)) != len(self.session_dirs)
            or not all(path.is_absolute() for path in self.session_dirs)
            or not self.required_session_dates
            or self.required_session_dates
            != tuple(sorted(set(self.required_session_dates)))
            or not 1 <= len(bindings) <= 3
            or len({item.strategy for item in bindings}) != len(bindings)
            or len({item.strategy_version for item in bindings}) != len(bindings)
            or len({item.queue_card_key for item in bindings}) != len(bindings)
            or any(
                not _canonical_text(item.strategy_version)
                or _HEX64.fullmatch(item.queue_card_key) is None
                for item in bindings
            )
            or _HEX40.fullmatch(self.dataset_producer_commit_sha) is None
            or _HEX40_OR_64.fullmatch(self.code_version) is None
            or not _aware(self.registered_at)
            or not 1 <= self.minimum_clean_sessions <= self.max_sessions <= 60
            or not 0 <= self.minimum_training_sessions < self.max_sessions
            or not 1 <= self.max_bars <= 100_000
            or self.per_side_fee_bps < 0
            or self.per_side_slippage_bps < 0
            or not 1 <= self.bootstrap_samples <= 100_000
            or not 0 < self.rss_limit_gib <= 64
        ):
            raise IntradayActualResearchPlanError
        return self


class IntradayActualResearchRunPlanContent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2, 3] = 3
    spec: IntradayActualResearchRunSpec
    source_queue_snapshot_id: str
    source_queue_artifact: Path

    @model_validator(mode="after")
    def validate_content(self) -> Self:
        if (
            _HEX64.fullmatch(self.source_queue_snapshot_id) is None
            or not self.source_queue_artifact.is_absolute()
            or (self.schema_version == 3)
            is not (self.spec.required_outcome_trace_schema_version is not None)
        ):
            raise IntradayActualResearchPlanError
        return self


class IntradayActualResearchRunPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2, 3] = 3
    plan_id: str
    content: IntradayActualResearchRunPlanContent

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        expected = hashlib.sha256(
            canonical_experiment_ledger_json(self.content).encode()
        ).hexdigest()
        if self.schema_version != self.content.schema_version or self.plan_id != expected:
            raise IntradayActualResearchPlanError
        return self


def create_intraday_actual_research_run_plan(
    content: IntradayActualResearchRunPlanContent,
) -> IntradayActualResearchRunPlan:
    return IntradayActualResearchRunPlan(
        schema_version=content.schema_version,
        plan_id=hashlib.sha256(
            canonical_experiment_ledger_json(content).encode()
        ).hexdigest(),
        content=content,
    )


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip() and not any(char in value for char in "\r\n\t")


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "IntradayActualResearchPlanError",
    "IntradayActualResearchPlanPaths",
    "IntradayActualResearchRunPlan",
    "IntradayActualResearchRunPlanContent",
    "IntradayActualResearchRunSpec",
    "create_intraday_actual_research_run_plan",
)

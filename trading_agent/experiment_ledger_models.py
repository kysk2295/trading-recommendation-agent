from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.lane_contract_models import ExperimentScope, ExperimentScopeKind
from trading_agent.lane_policy_models import LaneId
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class TrialKind(StrEnum):
    HISTORICAL_REPLAY = "historical_replay"
    SHADOW_FORWARD = "shadow_forward"
    BROKER_PAPER_FORWARD = "broker_paper_forward"
    EQUAL_RISK_COMPARISON = "equal_risk_comparison"
    CROSS_LANE_HYPOTHESIS = "cross_lane_hypothesis"


class TrialEventKind(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    CENSORED = "censored"


class StrategyLifecycleState(StrEnum):
    IDEA = "idea"
    HISTORICAL = "historical"
    EXPERIMENTAL_SHADOW = "experimental_shadow"
    EXPERIMENTAL_PAPER = "experimental_paper"
    CHALLENGER = "challenger"
    PAPER_CHAMPION = "paper_champion"
    SUSPENDED = "suspended"
    REJECTED = "rejected"


class StrategyLifecycleEventKind(StrEnum):
    REGISTRATION = "registration"
    TRANSITION = "transition"


class HypothesisRegistration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    hypothesis_id: str
    experiment_scope: ExperimentScope
    experiment_scope_key: str
    primary_lane: LaneId
    hypothesis: str
    falsification_rule: str
    source_registered_at: dt.datetime
    ledger_recorded_at: dt.datetime

    @model_validator(mode="after")
    def validate_registration(self) -> Self:
        if (
            not _identifier(self.hypothesis_id)
            or self.hypothesis_id != self.experiment_scope.hypothesis_id
            or self.experiment_scope_key != experiment_scope_key(self.experiment_scope)
            or self.primary_lane is not self.experiment_scope.primary_lane
            or not _canonical_text(self.hypothesis)
            or not _canonical_text(self.falsification_rule)
            or not _aware(self.source_registered_at)
            or not _aware(self.ledger_recorded_at)
            or self.source_registered_at != self.experiment_scope.registered_at
            or self.ledger_recorded_at < self.source_registered_at
        ):
            raise ValueError("invalid hypothesis registration")
        return self


class StrategyVersionRegistration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    strategy_id: str
    strategy_version: str
    hypothesis_id: str
    experiment_scope_key: str
    lane_id: LaneId
    code_version: str
    parameter_set: tuple[str, ...]
    data_contract: tuple[str, ...]
    cost_model: tuple[str, ...]
    portfolio_policy: tuple[str, ...]
    source_registered_at: dt.datetime
    ledger_recorded_at: dt.datetime

    @model_validator(mode="after")
    def validate_registration(self) -> Self:
        identities = (
            self.strategy_id,
            self.strategy_version,
            self.hypothesis_id,
            self.code_version,
        )
        contracts = (
            self.parameter_set,
            self.data_contract,
            self.cost_model,
            self.portfolio_policy,
        )
        if (
            not all(_identifier(value) for value in identities)
            or not _HEX64.fullmatch(self.experiment_scope_key)
            or not all(_ordered_contract(values) for values in contracts)
            or not _aware(self.source_registered_at)
            or not _aware(self.ledger_recorded_at)
            or self.ledger_recorded_at < self.source_registered_at
        ):
            raise ValueError("invalid strategy version registration")
        return self


class ExperimentTrialRegistration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    trial_id: str
    strategy_version: str
    trial_kind: TrialKind
    experiment_scope: ExperimentScope
    experiment_scope_key: str
    evaluator_version: str
    data_version: str
    feed_entitlement: str
    planned_start: dt.date
    planned_end: dt.date
    registered_at: dt.datetime
    evidence_budget: tuple[str, ...]

    @model_validator(mode="after")
    def validate_registration(self) -> Self:
        start_bounds = regular_session_bounds(self.planned_start)
        end_bounds = regular_session_bounds(self.planned_end)
        cross_lane = self.experiment_scope.scope_kind is ExperimentScopeKind.CROSS_LANE_HYPOTHESIS
        if (
            not _identifier(self.trial_id)
            or not _identifier(self.strategy_version)
            or not _identifier(self.evaluator_version)
            or not _HEX64.fullmatch(self.data_version)
            or not _canonical_text(self.feed_entitlement)
            or self.experiment_scope_key != experiment_scope_key(self.experiment_scope)
            or not _aware(self.registered_at)
            or start_bounds is None
            or end_bounds is None
            or self.planned_end < self.planned_start
            or (start_bounds is not None and self.registered_at >= start_bounds[0])
            or not _canonical_set(self.evidence_budget, required=True)
            or (self.trial_kind is TrialKind.CROSS_LANE_HYPOTHESIS) is not cross_lane
        ):
            raise ValueError("invalid experiment trial registration")
        return self


class ExperimentTrialEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    trial_id: str
    sequence: int
    event_kind: TrialEventKind
    occurred_at: dt.datetime
    artifact_sha256s: tuple[str, ...]
    reason_codes: tuple[str, ...]
    previous_event_key: str | None

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        if (
            not _identifier(self.trial_id)
            or self.sequence < 1
            or not _aware(self.occurred_at)
            or not _canonical_hashes(self.artifact_sha256s)
            or not _canonical_set(self.reason_codes)
        ):
            raise ValueError("invalid experiment trial event")
        if self.event_kind is TrialEventKind.STARTED:
            if self.sequence != 1 or self.previous_event_key is not None or self.artifact_sha256s or self.reason_codes:
                raise ValueError("invalid started experiment trial event")
            return self
        if self.sequence < 2 or self.previous_event_key is None or not _HEX64.fullmatch(self.previous_event_key):
            raise ValueError("invalid terminal experiment trial chain")
        if self.event_kind is TrialEventKind.COMPLETED:
            if not self.artifact_sha256s or self.reason_codes:
                raise ValueError("completed experiment trial requires artifacts")
            return self
        if not self.reason_codes:
            raise ValueError("failed or censored experiment trial requires reasons")
        return self


class StrategyLifecycleEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    strategy_version: str
    sequence: int
    event_kind: StrategyLifecycleEventKind
    from_state: StrategyLifecycleState | None
    to_state: StrategyLifecycleState
    policy_version: str
    decision_session_date: dt.date
    effective_session_date: dt.date
    decided_at: dt.datetime
    evidence_keys: tuple[str, ...]
    reason_codes: tuple[str, ...]
    previous_event_key: str | None

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        if (
            not _identifier(self.strategy_version)
            or not _identifier(self.policy_version)
            or self.sequence < 1
            or not _aware(self.decided_at)
            or self.decided_at.astimezone(NEW_YORK).date() != self.decision_session_date
            or self.effective_session_date <= self.decision_session_date
            or regular_session_bounds(self.effective_session_date) is None
            or not _canonical_hashes(self.evidence_keys, required=True)
            or not _canonical_set(self.reason_codes, required=True)
        ):
            raise ValueError("invalid strategy lifecycle event")
        if self.event_kind is StrategyLifecycleEventKind.REGISTRATION:
            return self._validate_registration()
        return self._validate_transition()

    def _validate_registration(self) -> Self:
        allowed_initial = {
            StrategyLifecycleState.IDEA,
            StrategyLifecycleState.HISTORICAL,
            StrategyLifecycleState.EXPERIMENTAL_SHADOW,
            StrategyLifecycleState.EXPERIMENTAL_PAPER,
        }
        if (
            self.sequence != 1
            or self.from_state is not None
            or self.previous_event_key is not None
            or self.to_state not in allowed_initial
        ):
            raise ValueError("invalid lifecycle registration")
        if self.to_state is StrategyLifecycleState.IDEA:
            if "new_strategy_registration" not in self.reason_codes:
                raise ValueError("new strategy registration reason is missing")
            return self
        if "existing_contract_import" not in self.reason_codes or len(self.evidence_keys) < 3:
            raise ValueError("existing strategy import evidence is missing")
        return self

    def _validate_transition(self) -> Self:
        if (
            self.sequence < 2
            or self.from_state is None
            or self.previous_event_key is None
            or not _HEX64.fullmatch(self.previous_event_key)
            or not lifecycle_transition_allowed(self.from_state, self.to_state)
        ):
            raise ValueError("invalid lifecycle transition")
        return self


def lifecycle_transition_allowed(
    from_state: StrategyLifecycleState,
    to_state: StrategyLifecycleState,
) -> bool:
    allowed = {
        StrategyLifecycleState.IDEA: {
            StrategyLifecycleState.HISTORICAL,
            StrategyLifecycleState.REJECTED,
        },
        StrategyLifecycleState.HISTORICAL: {
            StrategyLifecycleState.EXPERIMENTAL_SHADOW,
            StrategyLifecycleState.REJECTED,
        },
        StrategyLifecycleState.EXPERIMENTAL_SHADOW: {
            StrategyLifecycleState.EXPERIMENTAL_PAPER,
            StrategyLifecycleState.CHALLENGER,
            StrategyLifecycleState.SUSPENDED,
            StrategyLifecycleState.REJECTED,
        },
        StrategyLifecycleState.EXPERIMENTAL_PAPER: {
            StrategyLifecycleState.CHALLENGER,
            StrategyLifecycleState.SUSPENDED,
            StrategyLifecycleState.REJECTED,
        },
        StrategyLifecycleState.CHALLENGER: {
            StrategyLifecycleState.PAPER_CHAMPION,
            StrategyLifecycleState.SUSPENDED,
            StrategyLifecycleState.REJECTED,
        },
        StrategyLifecycleState.PAPER_CHAMPION: {StrategyLifecycleState.SUSPENDED},
        StrategyLifecycleState.SUSPENDED: {
            StrategyLifecycleState.EXPERIMENTAL_SHADOW,
            StrategyLifecycleState.EXPERIMENTAL_PAPER,
            StrategyLifecycleState.CHALLENGER,
            StrategyLifecycleState.PAPER_CHAMPION,
            StrategyLifecycleState.REJECTED,
        },
        StrategyLifecycleState.REJECTED: set(),
    }
    return to_state in allowed[from_state]


def lifecycle_state_rank(state: StrategyLifecycleState) -> int:
    return {
        StrategyLifecycleState.IDEA: 0,
        StrategyLifecycleState.HISTORICAL: 1,
        StrategyLifecycleState.EXPERIMENTAL_SHADOW: 2,
        StrategyLifecycleState.EXPERIMENTAL_PAPER: 3,
        StrategyLifecycleState.CHALLENGER: 4,
        StrategyLifecycleState.PAPER_CHAMPION: 5,
        StrategyLifecycleState.SUSPENDED: 6,
        StrategyLifecycleState.REJECTED: 7,
    }[state]


def _identifier(value: str) -> bool:
    return _IDENTIFIER.fullmatch(value) is not None


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip()


def _ordered_contract(values: tuple[str, ...]) -> bool:
    return bool(values) and len(values) == len(set(values)) and all(_canonical_text(value) for value in values)


def _canonical_set(values: tuple[str, ...], *, required: bool = False) -> bool:
    return (
        (bool(values) or not required)
        and values == tuple(sorted(set(values)))
        and all(_identifier(value) for value in values)
    )


def _canonical_hashes(values: tuple[str, ...], *, required: bool = False) -> bool:
    return (
        (bool(values) or not required)
        and values == tuple(sorted(set(values)))
        and all(_HEX64.fullmatch(value) for value in values)
    )

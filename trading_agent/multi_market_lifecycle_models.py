from __future__ import annotations

import datetime as dt
import re
from typing import Literal, Self, assert_never

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
    lifecycle_transition_allowed,
)
from trading_agent.multi_market_trial_models import market_local_date
from trading_agent.research_identity_models import StrategyLaneRef

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class InvalidMultiMarketLifecycleModelError(ValueError):
    def __str__(self) -> str:
        return "invalid multi-market lifecycle model"


class MultiMarketStrategyLifecycleEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    strategy_version: str
    strategy_lane: StrategyLaneRef
    sequence: int
    event_kind: StrategyLifecycleEventKind
    from_state: StrategyLifecycleState | None
    to_state: StrategyLifecycleState
    policy_version: str
    decision_session_date: dt.date
    effective_session_date: dt.date
    decided_at: dt.datetime
    session_calendar_snapshot_id: str
    evidence_keys: tuple[str, ...]
    reason_codes: tuple[str, ...]
    previous_event_key: str | None

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        if (
            _IDENTIFIER.fullmatch(self.strategy_version) is None
            or _IDENTIFIER.fullmatch(self.policy_version) is None
            or self.sequence < 1
            or not _aware(self.decided_at)
            or market_local_date(self.strategy_lane.market_id, self.decided_at) != self.decision_session_date
            or self.effective_session_date <= self.decision_session_date
            or _HEX64.fullmatch(self.session_calendar_snapshot_id) is None
            or not _canonical_hashes(self.evidence_keys)
            or not _canonical_reasons(self.reason_codes)
        ):
            raise InvalidMultiMarketLifecycleModelError
        match self.event_kind:
            case StrategyLifecycleEventKind.REGISTRATION:
                if (
                    self.sequence != 1
                    or self.from_state is not None
                    or self.to_state is not StrategyLifecycleState.EXPERIMENTAL_SHADOW
                    or self.previous_event_key is not None
                    or self.reason_codes != ("multi_market_strategy_registered",)
                ):
                    raise InvalidMultiMarketLifecycleModelError
            case StrategyLifecycleEventKind.TRANSITION:
                if (
                    self.sequence < 2
                    or self.from_state is None
                    or self.previous_event_key is None
                    or _HEX64.fullmatch(self.previous_event_key) is None
                    or not lifecycle_transition_allowed(self.from_state, self.to_state)
                ):
                    raise InvalidMultiMarketLifecycleModelError
            case unreachable:
                assert_never(unreachable)
        return self


def _canonical_hashes(values: tuple[str, ...]) -> bool:
    return bool(values) and values == tuple(sorted(set(values))) and all(_HEX64.fullmatch(value) for value in values)


def _canonical_reasons(values: tuple[str, ...]) -> bool:
    return (
        bool(values) and values == tuple(sorted(set(values))) and all(_IDENTIFIER.fullmatch(value) for value in values)
    )


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None

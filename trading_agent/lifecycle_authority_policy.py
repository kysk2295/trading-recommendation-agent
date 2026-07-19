from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from trading_agent.experiment_ledger_keys import strategy_authority_binding_key
from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEvent,
    StrategyLifecycleState,
)
from trading_agent.research_identity_models import AgentOperatingMode
from trading_agent.strategy_authority_models import StrategyAuthorityBinding


@dataclass(frozen=True, slots=True)
class InvalidLifecycleAuthorityError(ValueError):
    def __str__(self) -> str:
        return "lifecycle champion authority is invalid"


def require_new_champion_authority(
    binding: StrategyAuthorityBinding | None,
    prior_events: tuple[StrategyLifecycleEvent, ...],
    event: StrategyLifecycleEvent,
) -> None:
    required_mode = _required_champion_mode(event.to_state)
    if required_mode is None:
        return
    _require_exact_authority(binding, event, required_mode)
    _require_champion_path(prior_events, event.to_state)


def require_persisted_champion_authority(
    binding: StrategyAuthorityBinding | None,
    events: tuple[StrategyLifecycleEvent, ...],
) -> None:
    for index, event in enumerate(events):
        required_mode = _required_champion_mode(event.to_state)
        if required_mode is None:
            continue
        if _is_legacy_paper_champion(binding, event):
            continue
        _require_exact_authority(binding, event, required_mode)
        _require_champion_path(events[:index], event.to_state)


def _require_exact_authority(
    binding: StrategyAuthorityBinding | None,
    event: StrategyLifecycleEvent,
    required_mode: AgentOperatingMode,
) -> None:
    if (
        binding is None
        or binding.operating_mode is not required_mode
        or event.decided_at < binding.bound_at
        or str(strategy_authority_binding_key(binding)) not in event.evidence_keys
    ):
        raise InvalidLifecycleAuthorityError


def _require_champion_path(
    prior_events: tuple[StrategyLifecycleEvent, ...],
    target: StrategyLifecycleState,
) -> None:
    has_paper_phase = any(event.to_state is StrategyLifecycleState.EXPERIMENTAL_PAPER for event in prior_events)
    match target:
        case StrategyLifecycleState.SHADOW_CHAMPION:
            if has_paper_phase:
                raise InvalidLifecycleAuthorityError
        case StrategyLifecycleState.PAPER_CHAMPION:
            if not has_paper_phase:
                raise InvalidLifecycleAuthorityError
        case (
            StrategyLifecycleState.IDEA
            | StrategyLifecycleState.HISTORICAL
            | StrategyLifecycleState.EXPERIMENTAL_SHADOW
            | StrategyLifecycleState.EXPERIMENTAL_PAPER
            | StrategyLifecycleState.CHALLENGER
            | StrategyLifecycleState.SUSPENDED
            | StrategyLifecycleState.REJECTED
        ):
            return
        case unreachable:
            assert_never(unreachable)


def _required_champion_mode(
    state: StrategyLifecycleState,
) -> AgentOperatingMode | None:
    match state:
        case StrategyLifecycleState.SHADOW_CHAMPION:
            return AgentOperatingMode.SHADOW
        case StrategyLifecycleState.PAPER_CHAMPION:
            return AgentOperatingMode.ALPACA_PAPER
        case (
            StrategyLifecycleState.IDEA
            | StrategyLifecycleState.HISTORICAL
            | StrategyLifecycleState.EXPERIMENTAL_SHADOW
            | StrategyLifecycleState.EXPERIMENTAL_PAPER
            | StrategyLifecycleState.CHALLENGER
            | StrategyLifecycleState.SUSPENDED
            | StrategyLifecycleState.REJECTED
        ):
            return None
        case unreachable:
            assert_never(unreachable)


def _is_legacy_paper_champion(
    binding: StrategyAuthorityBinding | None,
    event: StrategyLifecycleEvent,
) -> bool:
    return event.to_state is StrategyLifecycleState.PAPER_CHAMPION and (
        binding is None or event.decided_at < binding.bound_at
    )

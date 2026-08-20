from __future__ import annotations

import datetime as dt

from trading_agent.day_learning_policy import ExplorationPolicy
from trading_agent.day_strategy_capsule_models import (
    CapsuleArtifactKind,
    CapsuleAuthorityCeiling,
    StrategyCapsule,
)
from trading_agent.research_identity_models import MarketId
from trading_agent.us_forward_shadow_capsule_runtime import advance_us_forward_shadow_capsule
from trading_agent.us_forward_shadow_models import (
    UsForwardShadowTick,
    UsForwardShadowTickResult,
    current_xnys_tick_at,
)
from trading_agent.us_forward_shadow_services import (
    InvalidUsForwardShadowRuntimeError,
    UsForwardShadowServices,
)


def run_us_forward_shadow_tick(
    tick: UsForwardShadowTick,
    services: UsForwardShadowServices,
    *,
    evaluation_at: dt.datetime,
) -> UsForwardShadowTickResult:
    checked = validate_current_us_forward_shadow_tick(tick, evaluation_at=evaluation_at)
    reader = services.ledger.reader()
    policy = _exact_policy(checked, reader.day_exploration_policies(MarketId.US_EQUITIES))
    capsules = _active_capsules(policy, services)
    states = reader.day_forward_trials(MarketId.US_EQUITIES)
    by_capsule = {
        state.trial.capsule_id: state
        for state in states
        if state.trial.session_id == checked.session_id
    }
    results = tuple(
        advance_us_forward_shadow_capsule(
            checked, capsule, by_capsule.get(capsule.capsule_id), services
        )
        for capsule in capsules
    )
    return UsForwardShadowTickResult(
        policy_id=policy.policy_id,
        session_id=checked.session_id,
        completed_bar_id=checked.completed_bar_id,
        results=results,
    )


def validate_current_us_forward_shadow_tick(
    tick: UsForwardShadowTick,
    *,
    evaluation_at: dt.datetime,
) -> UsForwardShadowTick:
    checked = UsForwardShadowTick.model_validate(tick.model_dump(mode="python"))
    if not current_xnys_tick_at(checked, evaluation_at):
        raise InvalidUsForwardShadowRuntimeError("tick_not_current")
    return checked


def _exact_policy(
    tick: UsForwardShadowTick,
    policies: tuple[ExplorationPolicy, ...],
) -> ExplorationPolicy:
    matches = tuple(policy for policy in policies if policy.policy_id == tick.policy_id)
    if len(matches) != 1:
        raise InvalidUsForwardShadowRuntimeError("policy_missing")
    policy = matches[0]
    payload = policy.payload
    if (
        payload.market_id is not MarketId.US_EQUITIES
        or payload.effective_session_date != tick.session_date
        or payload.calendar_snapshot_id != tick.calendar_snapshot_id
        or payload.effective_at > tick.observed_at
        or len(payload.active_capsule_ids) > 3
    ):
        raise InvalidUsForwardShadowRuntimeError("policy_not_effective")
    return policy


def _active_capsules(
    policy: ExplorationPolicy,
    services: UsForwardShadowServices,
) -> tuple[StrategyCapsule, ...]:
    capsules: list[StrategyCapsule] = []
    reader = services.ledger.reader()
    for capsule_id in policy.payload.active_capsule_ids:
        stored = reader.day_strategy_capsule(capsule_id)
        if stored is None:
            raise InvalidUsForwardShadowRuntimeError("capsule_missing")
        capsule = stored.capsule
        if (
            capsule.market_id is not MarketId.US_EQUITIES
            or capsule.artifact_kind is not CapsuleArtifactKind.GENERATED_PYTHON
            or capsule.authority_ceiling is not CapsuleAuthorityCeiling.RESEARCH_ONLY
            or capsule.generated_artifact_id is None
        ):
            raise InvalidUsForwardShadowRuntimeError("capsule_authority_invalid")
        artifact = services.generated_artifacts.load(capsule.generated_artifact_id)
        if artifact.payload.source_sha256 != capsule.artifact_sha256:
            raise InvalidUsForwardShadowRuntimeError("capsule_artifact_mismatch")
        capsules.append(capsule)
    return tuple(capsules)


__all__ = (
    "InvalidUsForwardShadowRuntimeError",
    "UsForwardShadowServices",
    "run_us_forward_shadow_tick",
    "validate_current_us_forward_shadow_tick",
)

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from tests.day_agent_version_learning_support import SESSION
from tests.test_us_forward_shadow_models import _signal_artifact
from tests.us_forward_shadow_support import prepared_runtime, shadow_tick
from trading_agent.day_agent_challenger_evaluation import (
    DayForwardShadowSessionEvidence,
    DayForwardShadowSessionRequest,
    DayForwardShadowTickRequest,
)
from trading_agent.day_forward_trial_identity import DayForwardExitReason
from trading_agent.day_learning_policy import ExplorationPolicy, ExplorationPolicyPayload
from trading_agent.day_strategy_capsule import _publish_prebuilt_day_strategy_capsule
from trading_agent.day_strategy_capsule_models import StrategyCapsule
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.us_forward_shadow_artifacts import (
    UsForwardShadowOutcomeLeg,
    build_us_forward_shadow_outcome_artifact,
    build_us_forward_shadow_signal_artifact,
)
from trading_agent.us_forward_shadow_models import (
    UsForwardShadowCapsuleResult,
    UsForwardShadowStatus,
    UsForwardShadowTickResult,
    completed_bar_id,
)


def dual_capsule_runtime(root: Path):
    from tests.us_forward_shadow_support import signal_source

    services, champion_capsule = prepared_runtime(root, source=signal_source())
    payload = champion_capsule.model_dump(mode="python") | {
        "capsule_id": "",
        "slippage_model_id": "bounded_intraday_slippage_v2",
    }
    challenger_capsule = StrategyCapsule.model_validate(
        payload | {"capsule_id": StrategyCapsule.canonical_id_for(payload)}
    )
    assert _publish_prebuilt_day_strategy_capsule(services.ledger, challenger_capsule)
    original = services.ledger.reader().day_exploration_policies()[0]
    policy_payload = ExplorationPolicyPayload.model_validate(
        original.payload.model_dump(mode="python")
        | {
            "final_report_id": "9" * 64,
            "effective_session_date": dt.date(2026, 8, 21),
            "effective_at": original.payload.effective_at + dt.timedelta(days=1),
            "active_capsule_ids": tuple(sorted((champion_capsule.capsule_id, challenger_capsule.capsule_id))),
        }
    )
    policy = ExplorationPolicy(
        policy_id=hashlib.sha256(canonical_experiment_ledger_json(policy_payload).encode()).hexdigest(),
        payload=policy_payload,
    )
    with services.ledger.writer() as writer:
        assert writer.record_day_exploration_policy(policy)
    return services, champion_capsule, challenger_capsule, policy


def session_request(services, policy_id: str, session_date: dt.date) -> DayForwardShadowSessionRequest:
    baseline = tuple(
        shadow_tick(
            services,
            minute,
            sequence,
            high=103.2 if sequence == 4 else None,
            policy_id=policy_id,
        )
        for sequence, minute in enumerate((1, 2, 3, 4), start=1)
    )
    delta = session_date - SESSION
    ticks = tuple(_shift_tick(item, delta, session_date) for item in baseline)
    return DayForwardShadowSessionRequest(
        ticks=tuple(DayForwardShadowTickRequest(tick=item, evaluation_at=item.observed_at) for item in ticks)
    )


def _shift_tick(tick, delta: dt.timedelta, session_date: dt.date):
    bars = tuple(item.model_copy(update={"timestamp": item.timestamp + delta}) for item in tick.bars)
    candidate = (
        None
        if tick.candidate is None
        else tick.candidate.model_copy(update={"timestamp": tick.candidate.timestamp + delta})
    )
    quote = tick.quote.model_copy(
        update={
            "observed_at": tick.quote.observed_at + delta,
            "valid_until": tick.quote.valid_until + delta,
        }
    )
    refs = tuple(item.model_copy(update={"observed_at": item.observed_at + delta}) for item in tick.evidence_refs)
    return tick.model_validate(
        tick.model_dump(mode="python")
        | {
            "session_id": f"XNYS-{session_date.isoformat()}",
            "session_date": session_date,
            "completed_bar_id": completed_bar_id(bars[-1]),
            "bars": bars,
            "candidate": candidate,
            "quote": quote,
            "evidence_refs": refs,
            "observed_at": tick.observed_at + delta,
        }
    )


@dataclass(frozen=True, slots=True)
class TypedControllerFake:
    champion_capsule_id: str
    challenger_capsule_id: str

    def run_session(
        self,
        request: DayForwardShadowSessionRequest,
        capsule_ids: tuple[str, str],
    ) -> DayForwardShadowSessionEvidence:
        assert capsule_ids == (self.champion_capsule_id, self.challenger_capsule_id)
        first = request.ticks[0].tick
        last = request.ticks[-1].tick
        champion_trial = hashlib.sha256(f"champion:{first.session_id}".encode()).hexdigest()
        challenger_trial = hashlib.sha256(f"challenger:{first.session_id}".encode()).hexdigest()
        signal = build_us_forward_shadow_signal_artifact(
            trial_id=challenger_trial,
            capsule_id=self.challenger_capsule_id,
            completed_bar_id=first.completed_bar_id,
            completed_bar_sequence=first.completed_bar_sequence,
            signal=_signal_artifact().signal,
        )
        entry = signal.signal.entry_price
        legs = (
            UsForwardShadowOutcomeLeg(
                target_label="r1",
                exit_completed_bar_id=last.completed_bar_id,
                exit_price=entry * Decimal("1.02"),
                exit_reason=DayForwardExitReason.TARGET,
                weight=Decimal("0.5"),
                gross_return=Decimal("0"),
            ),
            UsForwardShadowOutcomeLeg(
                target_label="r2",
                exit_completed_bar_id=last.completed_bar_id,
                exit_price=entry * Decimal("1.03"),
                exit_reason=DayForwardExitReason.TARGET,
                weight=Decimal("0.5"),
                gross_return=Decimal("0"),
            ),
        )
        outcome = build_us_forward_shadow_outcome_artifact(
            trial_id=challenger_trial,
            signal_artifact_id=signal.artifact_id,
            exit_completed_bar_id=last.completed_bar_id,
            exit_completed_bar_sequence=last.completed_bar_sequence,
            entry_price=entry,
            legs=legs,
            round_trip_cost_bps=Decimal("4"),
            exit_reason=DayForwardExitReason.TARGET,
            recorded_at=last.observed_at,
        )
        tick_results = tuple(
            UsForwardShadowTickResult(
                policy_id=item.tick.policy_id,
                session_id=item.tick.session_id,
                completed_bar_id=item.tick.completed_bar_id,
                results=(
                    UsForwardShadowCapsuleResult(
                        capsule_id=self.champion_capsule_id,
                        trial_id=champion_trial,
                        status=UsForwardShadowStatus.NO_SIGNAL,
                        event_ids=(hashlib.sha256(f"c:{item.tick.completed_bar_id}".encode()).hexdigest(),),
                    ),
                    UsForwardShadowCapsuleResult(
                        capsule_id=self.challenger_capsule_id,
                        trial_id=challenger_trial,
                        status=UsForwardShadowStatus.EXITED,
                        event_ids=(hashlib.sha256(f"x:{item.tick.completed_bar_id}".encode()).hexdigest(),),
                        outcome_id=outcome.outcome_id,
                    ),
                ),
            )
            for item in request.ticks
        )
        return DayForwardShadowSessionEvidence(
            session_id=first.session_id,
            session_date=first.session_date,
            completed_bar_ids=tuple(item.tick.completed_bar_id for item in request.ticks),
            tick_results=tick_results,
            signals=(signal,),
            outcomes=(outcome,),
        )


__all__ = ("TypedControllerFake", "dual_capsule_runtime", "session_request")

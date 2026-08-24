from __future__ import annotations

import hashlib
import stat

from pydantic import ValidationError

from tests.test_kr_day_capsule_adapter import _request
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluation
from trading_agent.kr_day_capsule_shadow_models import KrDayCapsuleShadowStatus
from trading_agent.kr_day_capsule_shadow_service import (
    KrDayCapsuleShadowBatchResult,
)
from trading_agent.kr_day_capsule_shadow_service import (
    run_kr_day_capsule_shadow_tick as _run_shadow_tick,
)
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.kr_day_decision_models import (
    KrDayConditionalPlan,
    KrDayDecisionEvent,
    KrDayDecisionEventPayload,
    KrDayDecisionEvidenceValue,
    KrDayDecisionReasonCode,
    KrDayDecisionStatus,
)
from trading_agent.kr_day_decision_service import run_kr_day_decision_tick
from trading_agent.kr_day_decision_store import KrDayDecisionStore
from trading_agent.kr_theme_day_setup import derive_kr_theme_day_setup


def run_authorized_kr_shadow_tick(
    store: KrDayCapsuleShadowStore,
    evaluations: tuple[KrDayCapsuleEvaluation, ...],
) -> KrDayCapsuleShadowBatchResult:
    decisions = KrDayDecisionStore(store.path.with_name(f"{store.path.stem}-decisions.sqlite3"))
    if store.path.parent.exists() and stat.S_IMODE(store.path.parent.stat().st_mode) != 0o700:
        return _run_shadow_tick(store, evaluations, decisions)
    for evaluation in evaluations:
        previous_shadow = store.latest(evaluation.capsule_id, evaluation.session_date.isoformat())
        if previous_shadow is not None and previous_shadow.status is KrDayCapsuleShadowStatus.ACTIVE:
            continue
        base = _request()
        standard_lineage = (
            evaluation.capsule_id == base.capsule.capsule_id
            and evaluation.hypothesis_version_id == base.capsule.hypothesis_version_id
            and evaluation.calendar_snapshot_id == base.calendar.snapshot_id
        )
        if standard_lineage:
            request = base.model_copy(
                update={
                    "opportunity": evaluation.setup_input.opportunity,
                    "bars": evaluation.setup_input.bars,
                    "market": evaluation.market,
                    "evaluated_at": evaluation.evaluated_at,
                    "max_slippage_bps": evaluation.setup_input.max_slippage_bps,
                }
            )
            input_sha = hashlib.sha256(canonical_experiment_ledger_json(request).encode()).hexdigest()
            if input_sha == evaluation.decision_input_sha256:
                _ = run_kr_day_decision_tick((request,), decisions)
            continue
        setup = derive_kr_theme_day_setup(evaluation.setup_input)
        ask = evaluation.market.ask_price
        if setup is None or ask is None:
            continue
        refs = tuple(
            sorted(
                {
                    *(item.canonical_id for item in evaluation.setup_input.opportunity.evidence_refs),
                    *(item.canonical_id for item in evaluation.market.evidence_refs),
                    *(bar.evidence_ref.canonical_id for bar in evaluation.setup_input.bars),
                    *(item.canonical_id for item in setup.evidence_refs),
                }
            )
        )
        try:
            plan = KrDayConditionalPlan(
                trigger_rule="Current exact fresh ask confirms the completed-bar reclaim.",
                trigger_price=ask,
                stop_price=setup.stop_price,
                target_prices=tuple(item.price for item in setup.targets),
                invalidation_rule=setup.invalidation_rule,
                valid_until=min(setup.valid_until, evaluation.setup_input.opportunity.valid_until),
                rationale=setup.rationale,
                evidence_refs=refs,
                capsule_id=evaluation.capsule_id,
                hypothesis_version_id=evaluation.hypothesis_version_id,
            )
        except (ValidationError, ValueError):
            continue
        previous = decisions.latest(
            evaluation.capsule_id,
            evaluation.opportunity_id,
            evaluation.session_date,
        )
        try:
            payload = KrDayDecisionEventPayload(
                capsule_id=evaluation.capsule_id,
                hypothesis_version_id=evaluation.hypothesis_version_id,
                opportunity_id=evaluation.opportunity_id,
                session_date=evaluation.session_date,
                symbol=evaluation.symbol,
                completed_bar_at=evaluation.completed_bar_cursor,
                observed_at=evaluation.evaluated_at,
                valid_until=plan.valid_until,
                status=KrDayDecisionStatus.ARMED,
                reason_codes=(KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,),
                conditional_plan=plan,
                evidence_refs=refs,
                observed_evidence=(
                    KrDayDecisionEvidenceValue(
                        name="decision_input_sha256",
                        value=evaluation.decision_input_sha256,
                    ),
                ),
                previous_event_id=None if previous is None else previous.event_id,
            )
        except (ValidationError, ValueError):
            continue
        event = KrDayDecisionEvent.model_validate(
            payload.model_dump(mode="python")
            | {"event_id": KrDayDecisionEvent.canonical_id_for(payload)}
        )
        _ = decisions.append(event)
    return _run_shadow_tick(store, evaluations, decisions)


__all__ = ("run_authorized_kr_shadow_tick",)

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from tests.day_agent_version_learning_support import SESSION, LeaderAuthor, diagnostics
from tests.day_strategy_capsule_support import bar, proposal
from tests.test_day_learning_report_models import _payload
from tests.us_forward_shadow_support import prepared_runtime, signal_source
from trading_agent.day_agent_challenger_publisher import (
    DayAgentFutureShadowSession,
    DayAgentGeneratedCapsulePublisher,
)
from trading_agent.day_agent_forward_shadow_controller import UsForwardShadowControllerRunner
from trading_agent.day_agent_loop_engineer import DayAgentLoopServices, run_loop_engineer
from trading_agent.day_agent_version_models import (
    AgentChangeProposal,
    AgentDeploymentState,
    AgentModelRoleBinding,
    AgentVersion,
    build_agent_version,
)
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.day_learning_policy import ExplorationPolicy
from trading_agent.day_learning_reports import seal_market_close_report
from trading_agent.day_strategy_capsule_models import StrategyCapsule


@dataclass(frozen=True, slots=True)
class LoopEvaluationFixture:
    store: DayAgentVersionStore
    baseline: AgentVersion
    challenger: AgentVersion
    proposal: AgentChangeProposal
    controller: UsForwardShadowControllerRunner
    champion_capsule: StrategyCapsule
    challenger_capsule: StrategyCapsule
    policies: tuple[ExplorationPolicy, ...]


def loop_evaluation(root: Path) -> LoopEvaluationFixture:
    shadow_services, champion_capsule = prepared_runtime(root / "shadow", source=signal_source())
    baseline = build_agent_version(
        model_role_bindings=(AgentModelRoleBinding(role="reasoning", model_id="reasoner-v1"),),
        prompt_sha256="1" * 64,
        tool_policy_sha256="2" * 64,
        memory_retrieval_policy_sha256="3" * 64,
        playbook_ids=(champion_capsule.capsule_id,),
        parent_version_id=None,
        creation_evidence_ids=("a" * 64,),
        deployment_state=AgentDeploymentState.CHAMPION,
        task_id="task-20260820-NVDA",
        created_at=dt.datetime(2026, 8, 20, 14, 0, tzinfo=dt.UTC),
        created_session_date=SESSION,
    )
    store = DayAgentVersionStore(root / "versions.sqlite3")
    with store.writer() as writer:
        assert writer.register_initial_champion(baseline)
    base_payload = _payload()
    report = seal_market_close_report(
        base_payload.model_copy(
            update={
                "agent_version_id": baseline.version_id,
                "diagnostics": diagnostics(),
                "next_session": base_payload.next_session.model_copy(
                    update={"active_capsule_ids": (champion_capsule.capsule_id,), "queued_capsule_ids": ()}
                ),
            }
        )
    )
    future_sessions = tuple(
        DayAgentFutureShadowSession(
            session_date=session_date,
            calendar_snapshot_id="calendar://official/XNYS/2026-v1",
            effective_at=dt.datetime.combine(session_date, dt.time(13, 30), tzinfo=dt.UTC),
        )
        for session_date in (dt.date(2026, 8, 21), dt.date(2026, 8, 24))
    )
    proposal_record = run_loop_engineer(
        report,
        baseline,
        DayAgentLoopServices(
            store=store,
            author=LeaderAuthor(),
            publisher=DayAgentGeneratedCapsulePublisher(
                services=shadow_services,
                proposal_template=proposal(signal_source()),
                replay_bars=(bar(),),
                future_sessions=future_sessions,
            ),
        ),
    )
    challenger = store.reader().challenger(proposal_record.version_id)
    assert challenger is not None
    stored_capsule = shadow_services.ledger.reader().day_strategy_capsule(challenger.playbook_ids[0])
    assert stored_capsule is not None
    policies = tuple(
        policy
        for policy in shadow_services.ledger.reader().day_exploration_policies()
        if policy.payload.effective_session_date > SESSION
    )
    return LoopEvaluationFixture(
        store,
        baseline,
        challenger,
        proposal_record,
        UsForwardShadowControllerRunner(shadow_services),
        champion_capsule,
        stored_capsule.capsule,
        policies,
    )


__all__ = ("LoopEvaluationFixture", "loop_evaluation")

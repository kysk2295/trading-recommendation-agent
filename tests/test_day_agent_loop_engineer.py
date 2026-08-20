from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.test_day_learning_report_models import NOW, SHA_A, SHA_B, _payload
from trading_agent.day_agent_challenger_evaluation import (
    DayShadowComparisonInput,
    DayShadowSnapshotScore,
    evaluate_day_agent_challenger,
)
from trading_agent.day_agent_loop_engineer import (
    DayAgentLoopServices,
    ProposedAgentChange,
    run_loop_engineer,
)
from trading_agent.day_agent_version_models import (
    AgentChangeKind,
    AgentDeploymentState,
    AgentModelRoleBinding,
    AgentPromotionDecision,
    AgentVersion,
    DayAgentVersionStoreError,
    build_agent_version,
)
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.day_learning_report_models import (
    DayDecisionDiagnostic,
    DayDecisionOutcome,
    DayDecisionStage,
)
from trading_agent.day_learning_reports import FinalizedDayDecisionEvidence, build_day_decision_diagnostics

SESSION = dt.date(2026, 8, 20)


def _diagnostics(*, leader_score: float = 0.1) -> tuple[DayDecisionDiagnostic, ...]:
    return tuple(
        DayDecisionDiagnostic(
            stage=stage,
            outcome=DayDecisionOutcome.SUPPORTED,
            score=leader_score if stage is DayDecisionStage.LEADER_SELECTION else 0.8,
            evidence_ids=(SHA_A,),
            reason_codes=("leader_rank_late",) if stage is DayDecisionStage.LEADER_SELECTION else ("supported",),
        )
        for stage in DayDecisionStage
    )


def _champion() -> AgentVersion:
    return build_agent_version(
        model_role_bindings=(AgentModelRoleBinding(role="reasoning", model_id="reasoner-v1"),),
        prompt_sha256="1" * 64,
        tool_policy_sha256="2" * 64,
        memory_retrieval_policy_sha256="3" * 64,
        playbook_ids=("4" * 64,),
        parent_version_id=None,
        creation_evidence_ids=(SHA_A,),
        deployment_state=AgentDeploymentState.CHAMPION,
        task_id="task-20260820-NVDA",
        created_at=NOW,
        created_session_date=SESSION,
    )


@dataclass(frozen=True, slots=True)
class _LeaderAuthor:
    content: str = "rank leaders using catalyst freshness and relative volume"

    def propose(self, stage: DayDecisionStage, champion: AgentVersion) -> ProposedAgentChange:
        assert stage is DayDecisionStage.LEADER_SELECTION
        assert champion.deployment_state is AgentDeploymentState.CHAMPION
        return ProposedAgentChange(kind=AgentChangeKind.LEADER_RANKING_POLICY, content=self.content)


def test_close_diagnostics_require_finalized_paper_and_market_evidence() -> None:
    # Given: all eight stage observations after the report finalization watermark.
    evidence = FinalizedDayDecisionEvidence(
        agent_version_id="a" * 64,
        recommendation_event_ids=(SHA_A,),
        market_event_ids=(SHA_B,),
        paper_event_ids=("c" * 64,),
        finalized_at=NOW,
        stage_scores=tuple((stage, 0.75) for stage in DayDecisionStage),
        stage_reason_codes=tuple((stage, ("supported",)) for stage in DayDecisionStage),
    )

    # When: diagnostics are built at the exact market watermark.
    diagnostics = build_day_decision_diagnostics(evidence, watermark=_payload().watermark)

    # Then: every stage is separately scored with immutable evidence and no profit prose field.
    assert tuple(item.stage for item in diagnostics) == tuple(DayDecisionStage)
    assert all(item.evidence_ids == (SHA_A, SHA_B, "c" * 64) for item in diagnostics)
    assert all("profit" not in field for field in DayDecisionDiagnostic.model_fields)


def test_loop_engineer_turns_leader_error_into_shadow_challenger(tmp_path: Path) -> None:
    # Given: an explicit Champion and a finalized report whose weakest stage is leader selection.
    store = DayAgentVersionStore(tmp_path / "versions.sqlite3")
    champion = _champion()
    with store.writer() as writer:
        assert writer.register_initial_champion(champion)
    report = _payload().model_copy(
        update={"agent_version_id": champion.version_id, "diagnostics": _diagnostics()}
    )

    # When: the Loop Engineer proposes its single bounded change.
    proposal = run_loop_engineer(report, champion, DayAgentLoopServices(store=store, author=_LeaderAuthor()))

    # Then: the stored version is a research-only Shadow and Dashboard can query it after restart.
    assert proposal.problem_stage is DayDecisionStage.LEADER_SELECTION
    assert proposal.allowed_changes == (AgentChangeKind.LEADER_RANKING_POLICY,)
    assert proposal.change_content == _LeaderAuthor().content
    challenger = DayAgentVersionStore(store.path).reader().challenger(proposal.version_id)
    assert challenger is not None
    assert challenger.order_authority is False
    assert challenger.deployment_state is AgentDeploymentState.SHADOW
    assert DayAgentVersionStore(store.path).reader().proposals(proposal.version_id) == (proposal,)
    views = DayAgentVersionStore(store.path).reader().versions()
    assert {view.deployment_state for view in views} == {"champion", "shadow"}


@pytest.mark.parametrize(
    "content",
    (
        "change endpoint to a different broker",
        "read credential from another file",
        "increase account risk and order quantity",
        "disable safety gates",
        "lower promotion thresholds",
        "delete audit history",
    ),
)
def test_loop_engineer_rejects_prohibited_change_before_storage(tmp_path: Path, content: str) -> None:
    # Given: a valid Champion but an authored change touching protected authority.
    store = DayAgentVersionStore(tmp_path / "versions.sqlite3")
    champion = _champion()
    with store.writer() as writer:
        assert writer.register_initial_champion(champion)
    report = _payload().model_copy(
        update={"agent_version_id": champion.version_id, "diagnostics": _diagnostics()}
    )

    # When / Then: the proposal is rejected and no Challenger exists.
    with pytest.raises(DayAgentVersionStoreError, match="change_prohibited"):
        _ = run_loop_engineer(
            report,
            champion,
            DayAgentLoopServices(store=store, author=_LeaderAuthor(content)),
        )
    assert store.reader().challengers() == ()


def _snapshot(
    version: AgentVersion,
    session_date: dt.date,
    snapshot_id: str,
    score: float,
) -> DayShadowSnapshotScore:
    return DayShadowSnapshotScore(
        version_id=version.version_id,
        session_date=session_date,
        situation_snapshot_id=snapshot_id,
        theme_timing=score,
        leader_rank=score,
        recommendation_calibration=score,
        mfe=score,
        mae=-0.1,
        cost_adjusted_modeled_result=score,
        no_trade_quality=score,
        evidence_fidelity=score,
        forward_shadow_artifact_ids=(SHA_A,),
    )


def test_future_shadow_comparison_records_promote_without_deploying(tmp_path: Path) -> None:
    # Given: Champion and Challenger scores on the same two future session snapshots.
    store = DayAgentVersionStore(tmp_path / "versions.sqlite3")
    champion = _champion()
    challenger = build_agent_version(
        model_role_bindings=champion.model_role_bindings,
        prompt_sha256=champion.prompt_sha256,
        tool_policy_sha256="5" * 64,
        memory_retrieval_policy_sha256=champion.memory_retrieval_policy_sha256,
        playbook_ids=champion.playbook_ids,
        parent_version_id=champion.version_id,
        creation_evidence_ids=(SHA_A,),
        deployment_state=AgentDeploymentState.SHADOW,
        task_id=champion.task_id,
        created_at=NOW,
        created_session_date=SESSION,
    )
    with store.writer() as writer:
        assert writer.register_initial_champion(champion)
        assert writer.register_challenger(challenger)
    days = (dt.date(2026, 8, 21), dt.date(2026, 8, 24))
    comparison = DayShadowComparisonInput(
        champion=tuple(_snapshot(champion, day, f"snapshot-{day}", 0.5) for day in days),
        challenger=tuple(_snapshot(challenger, day, f"snapshot-{day}", 0.8) for day in days),
        minimum_sessions=2,
        evaluated_at=NOW + dt.timedelta(days=4),
    )

    # When: deterministic evaluation records its recommendation.
    recommendation = evaluate_day_agent_challenger(comparison, store)

    # Then: it recommends promotion but leaves deployment authority untouched.
    assert recommendation.decision is AgentPromotionDecision.PROMOTE
    assert store.reader().recommendations(challenger.version_id) == (recommendation,)
    assert store.reader().challenger(challenger.version_id) == challenger
    assert store.reader().champion() == champion


def test_same_session_or_unpaired_shadow_cannot_promote(tmp_path: Path) -> None:
    # Given: a Challenger with only its creation session and a mismatched situation snapshot.
    store = DayAgentVersionStore(tmp_path / "versions.sqlite3")
    champion = _champion()
    challenger = build_agent_version(
        model_role_bindings=champion.model_role_bindings,
        prompt_sha256="5" * 64,
        tool_policy_sha256=champion.tool_policy_sha256,
        memory_retrieval_policy_sha256=champion.memory_retrieval_policy_sha256,
        playbook_ids=champion.playbook_ids,
        parent_version_id=champion.version_id,
        creation_evidence_ids=(SHA_A,),
        deployment_state=AgentDeploymentState.SHADOW,
        task_id=champion.task_id,
        created_at=NOW,
        created_session_date=SESSION,
    )
    comparison = DayShadowComparisonInput(
        champion=(_snapshot(champion, SESSION, "snapshot-a", 0.4),),
        challenger=(_snapshot(challenger, SESSION, "snapshot-b", 0.9),),
        minimum_sessions=2,
        evaluated_at=NOW + dt.timedelta(hours=1),
    )

    # When / Then: evaluation fails closed before a recommendation is written.
    with pytest.raises(DayAgentVersionStoreError, match="future_shadow_pairing_invalid"):
        _ = evaluate_day_agent_challenger(comparison, store)
    assert store.reader().recommendations(challenger.version_id) == ()


def test_version_store_is_private_and_rejects_linked_paths(tmp_path: Path) -> None:
    # Given: a private version store and alternate hard-link and parent-symlink paths.
    path = tmp_path / "private" / "versions.sqlite3"
    store = DayAgentVersionStore(path)
    with store.writer() as writer:
        assert writer.register_initial_champion(_champion())
    hardlink = tmp_path / "hardlink.sqlite3"
    os.link(path, hardlink)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(path.parent, target_is_directory=True)

    # When / Then: mode is private and neither linked identity is accepted.
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(DayAgentVersionStoreError, match="metadata_invalid"):
        _ = DayAgentVersionStore(hardlink).reader().champion()
    with (
        pytest.raises(DayAgentVersionStoreError, match="metadata_invalid"),
        DayAgentVersionStore(linked_parent / "new.sqlite3").writer(),
    ):
        pass

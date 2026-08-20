from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.day_agent_version_learning_support import LeaderAuthor, champion, diagnostics
from tests.test_day_learning_report_models import _payload
from trading_agent.day_agent_loop_engineer import DayAgentLoopServices, run_loop_engineer
from trading_agent.day_agent_version_models import (
    AgentChangeKind,
    AgentDeploymentState,
    DayAgentVersionStoreError,
)
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.day_learning_report_models import DayDecisionStage


def test_loop_engineer_turns_leader_error_into_shadow_challenger(tmp_path: Path) -> None:
    # Given: an explicit Champion and a finalized report whose weakest stage is leader selection.
    store = DayAgentVersionStore(tmp_path / "versions.sqlite3")
    baseline = champion()
    with store.writer() as writer:
        assert writer.register_initial_champion(baseline)
    report = _payload().model_copy(update={"agent_version_id": baseline.version_id, "diagnostics": diagnostics()})

    # When: the Loop Engineer proposes its single bounded change.
    proposal = run_loop_engineer(
        report,
        baseline,
        DayAgentLoopServices(store=store, author=LeaderAuthor()),
    )

    # Then: the stored version is a research-only Shadow and Dashboard can query it after restart.
    assert proposal.problem_stage is DayDecisionStage.LEADER_SELECTION
    assert proposal.allowed_changes == (AgentChangeKind.LEADER_RANKING_POLICY,)
    assert proposal.patch.playbook_content == LeaderAuthor().content
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
        "InCrEaSe_risk-limits",
        "erase.audit records",
        "change-Alpaca_URL",
        "alter/order sizing",
        "bypass compliance guard",
    ),
)
def test_loop_engineer_rejects_prohibited_change_before_storage(
    tmp_path: Path,
    content: str,
) -> None:
    # Given: a valid Champion but an authored change touching protected authority.
    store = DayAgentVersionStore(tmp_path / "versions.sqlite3")
    baseline = champion()
    with store.writer() as writer:
        assert writer.register_initial_champion(baseline)
    report = _payload().model_copy(update={"agent_version_id": baseline.version_id, "diagnostics": diagnostics()})

    # When / Then: the proposal is rejected and no Challenger exists.
    with pytest.raises(DayAgentVersionStoreError, match="change_prohibited"):
        _ = run_loop_engineer(
            report,
            baseline,
            DayAgentLoopServices(store=store, author=LeaderAuthor(content)),
        )
    assert store.reader().challengers() == ()


def test_version_store_is_private_and_rejects_linked_paths(tmp_path: Path) -> None:
    # Given: a private version store and alternate hard-link and parent-symlink paths.
    path = tmp_path / "private" / "versions.sqlite3"
    store = DayAgentVersionStore(path)
    with store.writer() as writer:
        assert writer.register_initial_champion(champion())
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

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from tests.day_agent_loop_e2e_support import loop_evaluation
from tests.day_agent_version_learning_support import LeaderAuthor, champion, diagnostics
from tests.day_strategy_capsule_support import builtin_capsule
from tests.test_day_learning_report_models import _payload
from tests.us_forward_shadow_support import no_signal_source
from trading_agent import day_agent_loop_engineer
from trading_agent.day_agent_challenger_builders import DerivedSourceRequest, render_derived_source
from trading_agent.day_agent_challenger_publisher import (
    DayAgentChallengerPublicationRequest,
    PublishedDayAgentChallenger,
)
from trading_agent.day_agent_loop_engineer import DayAgentLoopServices, run_loop_engineer
from trading_agent.day_agent_version_models import (
    AgentChangeKind,
    AgentChangeProposal,
    AgentDeploymentState,
    AgentVersionPatch,
    DayAgentVersionStoreError,
    LeaderRankingFeature,
    LeaderRankingPatch,
)
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.day_learning_report_models import DayDecisionOutcome, DayDecisionStage
from trading_agent.day_learning_reports import seal_market_close_report
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json


def test_loop_engineer_turns_leader_error_into_shadow_challenger(tmp_path: Path) -> None:
    # Given: a finalized leader-selection diagnostic and concrete generated-capsule publisher.
    fixture = loop_evaluation(tmp_path)

    # When: the Loop Engineer persists its single bounded change.
    proposal = fixture.proposal

    # Then: the stored version references the exact persisted research-only capsule after restart.
    assert proposal.problem_stage is DayDecisionStage.LEADER_SELECTION
    assert proposal.allowed_changes == (AgentChangeKind.LEADER_RANKING_POLICY,)
    assert proposal.patch == LeaderRankingPatch(
        kind=AgentChangeKind.LEADER_RANKING_POLICY,
        feature=LeaderRankingFeature.RELATIVE_VOLUME,
        weight_bps=2_500,
    )
    challenger = DayAgentVersionStore(fixture.store.path).reader().challenger(proposal.version_id)
    assert challenger is not None
    assert challenger.order_authority is False
    assert challenger.deployment_state is AgentDeploymentState.SHADOW
    assert challenger.created_session_date.isoformat() == "2026-08-21"
    assert challenger.playbook_ids == (fixture.challenger_capsule.capsule_id,)
    assert DayAgentVersionStore(fixture.store.path).reader().proposals(proposal.version_id) == (proposal,)
    views = DayAgentVersionStore(fixture.store.path).reader().versions()
    assert {view.deployment_state for view in views} == {"champion", "shadow"}


def test_version_reader_preserves_legacy_proposals_without_report_identity(tmp_path: Path) -> None:
    # Given: an append-only proposal row persisted before source_report_id existed.
    fixture = loop_evaluation(tmp_path)
    legacy_payload = fixture.proposal.model_dump(mode="json")
    legacy_payload.pop("source_report_id")
    legacy_payload["proposal_id"] = "f" * 64
    with sqlite3.connect(fixture.store.path) as connection:
        _ = connection.execute(
            "INSERT INTO change_proposals VALUES (?,?,?)",
            (legacy_payload["proposal_id"], fixture.proposal.version_id, json.dumps(legacy_payload)),
        )

    # When: the public reader queries immutable proposal history and report identity.
    proposals = fixture.store.reader().proposals(fixture.proposal.version_id)
    report_proposal = fixture.store.reader().proposal_for_report(fixture.proposal.source_report_id)

    # Then: the legacy row remains readable but cannot impersonate report-bound lineage.
    assert tuple(item.source_report_id for item in proposals) == (
        fixture.proposal.source_report_id,
        None,
    )
    assert report_proposal == fixture.proposal


def test_new_change_proposal_requires_source_report_identity(tmp_path: Path) -> None:
    # Given: the payload for a newly authored proposal without its source report identity.
    fixture = loop_evaluation(tmp_path)
    payload = fixture.proposal.model_dump(mode="python")
    payload.pop("source_report_id")

    # When / Then: the public proposal model refuses new unbound records.
    with pytest.raises(ValidationError):
        _ = AgentChangeProposal.model_validate(payload)


def test_loop_engineer_rejects_diagnostics_without_refuted_stage(tmp_path: Path) -> None:
    # Given: a complete finalized diagnostic set whose stages are all supported.
    baseline = champion()
    payload = _payload().model_copy(
        update={
            "agent_version_id": baseline.version_id,
            "diagnostics": tuple(
                item.model_copy(update={"outcome": DayDecisionOutcome.SUPPORTED})
                for item in diagnostics()
            ),
        }
    )
    store = DayAgentVersionStore(tmp_path / "versions.sqlite3")
    with store.writer() as writer:
        assert writer.register_initial_champion(baseline)

    # When / Then: no author or publisher can turn insufficient failure evidence into a challenger.
    with pytest.raises(DayAgentVersionStoreError, match="loop_engineer_no_failing_stage"):
        _ = run_loop_engineer(
            seal_market_close_report(payload),
            baseline,
            DayAgentLoopServices(store, LeaderAuthor(), _UnexpectedPublisher()),
        )


@dataclass(frozen=True, slots=True)
class _UnexpectedPublisher:
    def publish(self, request: DayAgentChallengerPublicationRequest) -> PublishedDayAgentChallenger:
        raise AssertionError(request.report.report_id)


@pytest.mark.parametrize(
    "payload",
    (
        {"kind": "leader_ranking_policy", "feature": "relative_volume", "weight_bps": 2500, "text": "broker"},
        {"kind": "leader_ranking_policy", "feature": "risk_limit", "weight_bps": 2500},
        {"kind": "leader_ranking_policy", "feature": "\uff42\uff52\uff4f\uff4b\uff45\uff52", "weight_bps": 2500},
        {"kind": "leader_ranking_policy", "feature": "relative_volume", "weight_bps": "audit"},
    ),
)
def test_typed_patch_rejects_arbitrary_text_extra_keys_and_unicode(payload: dict[str, str | int]) -> None:
    # Given / When / Then: non-allowlisted patch shapes cannot cross the typed boundary.
    with pytest.raises(ValidationError):
        _ = TypeAdapter(AgentVersionPatch).validate_python(payload)


@pytest.mark.parametrize(
    "payload",
    (
        {"kind": "market_regime_policy", "rule": "trend_alignment", "confirmation_bars": 2},
        {"kind": "theme_selection_policy", "timing_window": "opening_30_minutes", "minimum_catalyst_count": 1},
        {"kind": "catalyst_interpretation_policy", "rule": "freshness_first", "maximum_age_minutes": 30},
        {"kind": "leader_ranking_policy", "feature": "relative_volume", "weight_bps": 2500},
        {"kind": "flow_interpretation_policy", "rule": "volume_confirmation", "confirmation_bars": 2},
        {"kind": "entry_policy", "rule": "breakout_confirmation", "confirmation_bars": 2},
        {"kind": "exit_policy", "rule": "trailing_structure", "trailing_window_bars": 5},
        {"kind": "execution_review_policy", "rule": "slippage_attribution", "review_window_sessions": 5},
    ),
)
def test_every_typed_patch_renders_an_executable_lineage_bound_wrapper(payload: dict[str, str | int]) -> None:
    # Given: one member of the closed typed-patch union and immutable parent identities.
    patch = TypeAdapter(AgentVersionPatch).validate_python(payload)
    baseline = champion()
    parent = builtin_capsule()

    # When: the deterministic generated Playbook wrapper is rendered.
    source = render_derived_source(
        DerivedSourceRequest(baseline, parent, patch, "9" * 64, no_signal_source())
    )
    challenger = day_agent_loop_engineer._challenger(
        baseline,
        patch,
        parent.capsule_id,
        ("a" * 64,),
        _payload(),
        created_session_date=dt.date(2026, 8, 21),
    )

    # Then: it compiles and carries machine-readable patch and parent lineage tokens.
    _ = compile(source, "<day-agent-challenger>", "exec")
    assert canonical_experiment_ledger_json(patch) in source
    assert baseline.version_id in source
    assert parent.capsule_id in source
    assert challenger.playbook_ids == (parent.capsule_id,)


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

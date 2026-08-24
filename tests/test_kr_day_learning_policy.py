from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

import pytest

from tests.day_agent_version_learning_support import LeaderAuthor, diagnostics
from tests.day_strategy_capsule_support import PUBLISHED_AT, builtin_request
from tests.kr_day_shadow_support import run_authorized_kr_shadow_tick as run_kr_day_capsule_shadow_tick
from tests.test_kr_day_capsule_shadow import _advance, _entry_evaluation
from tests.test_kr_day_market_close_report import _outcome, _request
from trading_agent.day_agent_challenger_publisher import (
    DayAgentChallengerPublicationRequest,
    PublishedDayAgentChallenger,
)
from trading_agent.day_agent_loop_engineer import DayAgentLoopServices
from trading_agent.day_agent_version_models import (
    AgentDeploymentState,
    AgentModelRoleBinding,
    AgentVersion,
    build_agent_version,
)
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.day_learning_policy import ExplorationPolicy, ExplorationPolicyAction
from trading_agent.day_strategy_capsule import build_strategy_capsule
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.kr_day_learning_policy import (
    InvalidKrDayLearningPolicyError,
    publish_kr_day_learning_policy,
)
from trading_agent.kr_day_loop_engineer import run_kr_day_loop_engineer
from trading_agent.kr_day_market_close_report import publish_kr_day_market_close_report
from trading_agent.research_identity_models import MarketId


def test_policy_uses_exact_latest_revision_and_official_next_xkrx_session(tmp_path: Path) -> None:
    # Given: two immutable revisions for the same finalized KR session.
    store = KrDayCapsuleShadowStore(tmp_path / "shadow" / "events.sqlite3")
    entry = _entry_evaluation()
    stopped = _advance(entry, low=Decimal("9900"), high=Decimal("10400"))
    _ = run_kr_day_capsule_shadow_tick(store, (entry,))
    _ = run_kr_day_capsule_shadow_tick(store, (stopped,))
    events = store.events()
    outcome = _outcome(events)
    first = publish_kr_day_market_close_report(tmp_path / "reports", _request(events, (outcome,)))
    revised = publish_kr_day_market_close_report(
        tmp_path / "reports",
        _request(events, (outcome,)).model_copy(update={"data_incident_ids": ("late",)}),
    )

    # When: policy construction is attempted from old and latest revisions.
    with pytest.raises(InvalidKrDayLearningPolicyError):
        _ = publish_kr_day_learning_policy(
            tmp_path / "reports",
            tmp_path / "policies",
            first.report,
            _request(events, (outcome,)).calendar_snapshot,
            ExplorationPolicyAction.KEEP,
        )
    result = publish_kr_day_learning_policy(
        tmp_path / "reports",
        tmp_path / "policies",
        revised.report,
        _request(events, (outcome,)).calendar_snapshot,
        ExplorationPolicyAction.KEEP,
    )

    # Then: the policy is future-only, XKRX-backed, bounded, and has no authority.
    assert result.created is True
    assert result.policy.payload.final_report_id == revised.report.report_id
    assert result.policy.payload.effective_session_date.isoformat() == "2026-08-26"
    assert result.policy.payload.calendar_snapshot_id.startswith("calendar://official/XKRX/")
    assert len(result.policy.payload.active_capsule_ids) <= 3
    assert all("authority" not in name for name in type(result.policy.payload).model_fields)


def test_policy_replay_dedupes(tmp_path: Path) -> None:
    # Given: a latest finalized report and exact official calendar.
    store = KrDayCapsuleShadowStore(tmp_path / "shadow" / "events.sqlite3")
    entry = _entry_evaluation()
    stopped = _advance(entry, low=Decimal("9900"), high=Decimal("10400"))
    _ = run_kr_day_capsule_shadow_tick(store, (entry,))
    _ = run_kr_day_capsule_shadow_tick(store, (stopped,))
    events = store.events()
    outcome = _outcome(events)
    request = _request(events, (outcome,))
    report = publish_kr_day_market_close_report(tmp_path / "reports", request).report

    # When: the same policy is published twice.
    first = publish_kr_day_learning_policy(
        tmp_path / "reports",
        tmp_path / "policies",
        report,
        request.calendar_snapshot,
        ExplorationPolicyAction.NO_TRADE,
    )
    replay = publish_kr_day_learning_policy(
        tmp_path / "reports",
        tmp_path / "policies",
        report,
        request.calendar_snapshot,
        ExplorationPolicyAction.NO_TRADE,
    )

    # Then: only one content-addressed policy artifact exists.
    assert (first.created, replay.created) == (True, False)
    assert first.policy == replay.policy
    assert len(tuple((tmp_path / "policies").glob("kr_day_policy_*.json"))) == 1


def test_kr_report_policy_drives_persisted_future_only_shadow_challenger(tmp_path: Path) -> None:
    # Given: an exact finalized KR report, its XKRX policy, and a persisted champion.
    shadow_store = KrDayCapsuleShadowStore(tmp_path / "shadow" / "events.sqlite3")
    entry = _entry_evaluation()
    stopped = _advance(entry, low=Decimal("9900"), high=Decimal("10400"))
    _ = run_kr_day_capsule_shadow_tick(shadow_store, (entry,))
    _ = run_kr_day_capsule_shadow_tick(shadow_store, (stopped,))
    events = shadow_store.events()
    champion = _champion(events[0].capsule_id)
    close_diagnostics = tuple(
        item.model_copy(update={"evidence_ids": (events[-1].event_id,)})
        for item in diagnostics()
    )
    request = _request(events, (_outcome(events),)).model_copy(
        update={"agent_version_id": champion.version_id, "diagnostics": close_diagnostics}
    )
    publication = publish_kr_day_market_close_report(tmp_path / "reports", request)
    report = publication.report
    policy = publish_kr_day_learning_policy(
        tmp_path / "reports",
        tmp_path / "policies",
        report,
        request.calendar_snapshot,
        ExplorationPolicyAction.KEEP,
    ).policy
    version_store = DayAgentVersionStore(tmp_path / "versions.sqlite3")
    with version_store.writer() as writer:
        assert writer.register_initial_champion(champion)

    # When: the Loop Engineer persists its generated challenger from that KR policy.
    result = run_kr_day_loop_engineer(
        report,
        publication.metrics,
        policy,
        DayAgentLoopServices(version_store, LeaderAuthor(), _KrPublisher(policy)),
    )
    replay = run_kr_day_loop_engineer(
        report,
        publication.metrics,
        policy,
        DayAgentLoopServices(version_store, LeaderAuthor(), _KrPublisher(policy)),
    )

    # Then: the stored version is SHADOW, authority-free, and begins only on the official future session.
    assert result.proposal is not None
    assert replay.proposal == result.proposal
    assert (result.challenger_count, replay.challenger_count) == (1, 1)
    assert len(version_store.reader().challengers()) == 1
    proposal = result.proposal
    challenger = version_store.reader().challenger(proposal.version_id)
    assert challenger is not None
    assert challenger.deployment_state is AgentDeploymentState.SHADOW
    assert challenger.order_authority is False
    assert challenger.created_session_date == policy.payload.effective_session_date
    assert challenger.created_session_date > report.payload.session_date


@dataclass(frozen=True, slots=True)
class _KrPublisher:
    policy: ExplorationPolicy

    def publish(self, request: DayAgentChallengerPublicationRequest) -> PublishedDayAgentChallenger:
        assert request.report.report_id == self.policy.payload.final_report_id
        return PublishedDayAgentChallenger(
            build_strategy_capsule(
                replace(
                    builtin_request(market_id=MarketId.KR_EQUITIES),
                    published_at=PUBLISHED_AT + dt.timedelta(seconds=1),
                )
            ),
            (self.policy,),
        )


def _champion(capsule_id: str) -> AgentVersion:
    return build_agent_version(
        model_role_bindings=(AgentModelRoleBinding(role="reasoning", model_id="reasoner-v1"),),
        prompt_sha256="1" * 64,
        tool_policy_sha256="2" * 64,
        memory_retrieval_policy_sha256="3" * 64,
        playbook_ids=(capsule_id,),
        parent_version_id=None,
        creation_evidence_ids=("a" * 64,),
        deployment_state=AgentDeploymentState.CHAMPION,
        task_id="task-20260824-KR",
        created_at=dt.datetime(2026, 8, 24, 6, 31, tzinfo=dt.UTC),
        created_session_date=dt.date(2026, 8, 24),
    )

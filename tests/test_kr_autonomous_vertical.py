from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest

from tests.autonomous_supervisor_fixtures import fixture_reasoner
from tests.kr_autonomous_vertical_support import run_vertical
from tests.test_autonomous_supervisor_execution import _runtime
from tests.test_autonomous_task_models import NOW as SUPERVISOR_NOW
from tests.test_autonomous_task_models import task_fixture
from tests.test_kr_autonomous_market_service import NOW, _calendar, _receipts
from tests.test_kr_autonomous_trade_planner import _request
from tests.test_kr_social_signal import _request as _social_request
from tests.test_kr_social_signal import _selected_posts
from tests.test_kr_virtual_position_engine import _bar
from trading_agent.autonomous_task_models import AutonomousAgentRole
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.browser_social_evidence_store import BrowserSocialEvidenceStore
from trading_agent.kr_autonomous_market_models import KrAutonomousMarketError
from trading_agent.kr_autonomous_market_service import KrCorroborationProjectionInput, project_kr_corroboration
from trading_agent.kr_autonomous_trade_models import (
    KrAutonomousCriticStatus,
    KrAutonomousNoTrade,
    KrAutonomousRejected,
    KrAutonomousTradeThesis,
    KrNoTradeReason,
    KrOpenVirtualExposure,
    thesis_id,
)
from trading_agent.kr_autonomous_trade_planner import criticize_kr_autonomous_trade, plan_kr_autonomous_trade
from trading_agent.kr_autonomous_trade_store import KrAutonomousTradeStore
from trading_agent.kr_social_signal_models import KrSocialVerificationState, normalize_kr_social_signal
from trading_agent.kr_social_signal_store import KrSocialSignalStore
from trading_agent.kr_virtual_position_engine import advance_kr_virtual_position, arm_kr_virtual_position
from trading_agent.kr_virtual_position_models import KrVirtualPositionReason, KrVirtualPositionState
from trading_agent.kr_virtual_position_store import KrVirtualPositionStore


@pytest.mark.parametrize("order", ("observer_delegates", "research_combines"))
def test_fixture_chain_reaches_terminal_virtual_outcome_and_exact_restart_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    order: Literal["observer_delegates", "research_combines"],
) -> None:
    # Given: bounded browser and KIS fixtures with no provider mutation authority.
    # When: autonomous roles carry the evidence through the public KR tools.
    result = run_vertical(tmp_path, monkeypatch, order)

    # Then: the fixture reaches a durable terminal outcome in a model-selected order.
    assert result.tool_order == (
        "social.signal.normalize",
        "kr.market.corroborate",
        "kr.trade.plan",
        "critic.request",
        "kr.virtual.execute",
        "kr.position.reconcile",
    )
    expected_roles = (
        (
            AutonomousAgentRole.MARKET_OBSERVER,
            AutonomousAgentRole.OPPORTUNITY,
            AutonomousAgentRole.TRADING,
            AutonomousAgentRole.CRITIC,
            AutonomousAgentRole.TRADING,
            AutonomousAgentRole.POSITION,
        )
        if order == "observer_delegates"
        else (
            AutonomousAgentRole.RESEARCH,
            AutonomousAgentRole.TRADING,
            AutonomousAgentRole.CRITIC,
            AutonomousAgentRole.TRADING,
            AutonomousAgentRole.POSITION,
        )
    )
    assert result.delegate_roles == expected_roles
    assert result.decision_kinds.count("tool_call") == 6
    assert result.decision_kinds.count("delegate") == len(expected_roles)
    assert result.decision_kinds[-1] == "complete"
    assert result.signal.repost_cluster_count == result.signal.independent_source_count == 2
    assert result.signal.evidence_ids == tuple(sorted(post.evidence_id for post in result.posts))
    assert result.market.social_signal_id == result.signal.signal_id
    assert result.market.receipt_count == len(result.market.receipt_sha256s) == 3
    recommendation = result.recommendation
    assert recommendation.timestamp == NOW
    assert recommendation.stop < recommendation.entry < recommendation.targets[0] < recommendation.targets[1]
    assert recommendation.quantity > 0 and recommendation.rationale and recommendation.counterevidence
    assert recommendation.evidence_refs == tuple(sorted({*result.signal.evidence_ids, *result.market.evidence_ids}))
    assert recommendation.critic_verdict_id == recommendation.critic_verdict.verdict_id
    assert recommendation.critic_verdict.thesis_id == recommendation.thesis_id
    assert recommendation.critic_verdict.proposal_id == recommendation.proposal_id
    assert recommendation.virtual_only and not recommendation.trading_authority
    assert result.terminal.state is KrVirtualPositionState.STOPPED
    assert result.terminal.reason is KrVirtualPositionReason.STOP_FIRST
    assert result.terminal.fill_time is not None and result.terminal.fill_time > recommendation.timestamp
    assert result.startup.open_position_count == 1
    assert result.startup.appended_event_count == result.startup.terminal_position_count == 0
    assert result.open_restart_before == result.open_restart_after
    assert json.loads(result.open_restart_after)[-1]["state"] == "ARMED"
    assert result.replay_before == result.replay_after
    assert json.loads(result.replay_json)["event_id"] == result.terminal.event_id
    assert result.calls == tuple(
        ("KIS", "GET", path)
        for path in (
            "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
        )
    )
    assert not any(term in path for _, _, path in result.calls for term in ("accno", "order", "balance"))
    for post in result.posts:
        assert not BrowserSocialEvidenceStore(result.services.browser_evidence_database).append(post)
    assert not KrSocialSignalStore(result.services.social_signal_database).append(result.signal)
    trade_database = result.services.trade_database
    position_database = result.services.position_database
    assert trade_database is not None and position_database is not None
    assert not KrAutonomousTradeStore(trade_database).append(recommendation)
    position_store = KrVirtualPositionStore(position_database)
    history = position_store.events(result.terminal.position_id)
    assert tuple(event.state for event in history) == (KrVirtualPositionState.ARMED, KrVirtualPositionState.STOPPED)
    assert history[1].previous_event_id == history[0].event_id
    assert not position_store.append(result.terminal)


def test_copied_only_social_evidence_cannot_masquerade_as_independent_corroboration() -> None:
    # Given: two copied posts from one independent source cluster.
    posts = _selected_posts()[:2]

    # When: the public normalizer derives cluster identity.
    signal = normalize_kr_social_signal(_social_request(posts), posts)

    # Then: post count cannot inflate verification strength.
    assert signal.post_count == 2
    assert signal.independent_source_count == 1
    assert signal.verification_state is KrSocialVerificationState.UNVERIFIED_SOCIAL


@pytest.mark.parametrize("unsafe_market", ("stale", "closed"))
def test_stale_or_closed_kis_fixture_fails_before_trade_planning(unsafe_market: str) -> None:
    # Given: a stale receipt set or a closed KRX calendar fixture.
    request = _request()
    receipts = _receipts()
    calendar = _calendar()
    if unsafe_market == "stale":
        receipts = tuple(replace(item, received_at=NOW - dt.timedelta(seconds=6)) for item in receipts)
    else:
        calendar = _calendar(open_day=False)

    # When/Then: current-session corroboration fails closed.
    with pytest.raises(KrAutonomousMarketError):
        _ = project_kr_corroboration(
            KrCorroborationProjectionInput(
                signal=request.social_signal,
                calendar_snapshot=calendar,
                receipts=receipts,
                observed_at=NOW,
            )
        )


@pytest.mark.parametrize("unsafe_plan", ("missing_spread", "duplicate"))
def test_unsafe_plan_is_an_explicit_no_trade_with_future_wake(unsafe_plan: str) -> None:
    # Given: missing spread or an already-open symbol and theme.
    request = _request()
    if unsafe_plan == "missing_spread":
        request = request.model_copy(update={"market": request.market.model_copy(update={"spread_bps": Decimal("-1")})})
        expected = (KrNoTradeReason.MISSING_SPREAD,)
    else:
        request = request.model_copy(
            update={
                "open_exposures": (KrOpenVirtualExposure(symbol=request.thesis.symbol, theme=request.thesis.theme),)
            }
        )
        expected = (KrNoTradeReason.DUPLICATE_SYMBOL, KrNoTradeReason.DUPLICATE_THEME)

    # When: the deterministic public planner evaluates it.
    outcome = plan_kr_autonomous_trade(request)

    # Then: no levels or authority are created and retry timing is explicit.
    assert isinstance(outcome, KrAutonomousNoTrade)
    assert outcome.reason_codes == expected and outcome.next_wake_at > outcome.timestamp
    assert outcome.virtual_only and not outcome.trading_authority


def test_post_reaction_discovery_and_critic_contradiction_are_rejected() -> None:
    # Given: post-reaction publication and a thesis that repeats its hypothesis as counterevidence.
    request = _request()
    late_signal = request.social_signal.model_copy(
        update={"earliest_published_at": request.market.market_response_at + dt.timedelta(microseconds=1)}
    )
    thesis = request.thesis.model_copy(update={"counterevidence": (request.thesis.hypothesis,)})
    thesis = KrAutonomousTradeThesis.model_validate(
        thesis.model_copy(update={"thesis_id": thesis_id(thesis)}).model_dump(mode="python")
    )

    # When: both artifacts cross the public Critic/planning boundaries.
    chronology = criticize_kr_autonomous_trade(request.model_copy(update={"social_signal": late_signal}))
    contradiction = plan_kr_autonomous_trade(request.model_copy(update={"thesis": thesis}))

    # Then: neither can become an approved recommendation.
    assert chronology.status is KrAutonomousCriticStatus.REJECTED
    assert isinstance(contradiction, KrAutonomousRejected)
    assert contradiction.critic_verdict_id is not None


def test_bar_gap_censors_and_nonfuture_bar_never_fills() -> None:
    # Given: an approved recommendation, its armed state, one pre-cutoff bar, and one gapped future bar.
    recommendation = plan_kr_autonomous_trade(_request())
    assert not isinstance(recommendation, KrAutonomousNoTrade | KrAutonomousRejected)
    armed = arm_kr_virtual_position(recommendation, recommendation.timestamp)
    local = recommendation.timestamp.astimezone(NOW.tzinfo).replace(second=0, microsecond=0)
    old = _bar(local, 31, low="100", high="108")
    gap = _bar(local + dt.timedelta(minutes=2), 32, low="102", high="104")

    # When: each bar set crosses the public future-only engine boundary.
    ignored = advance_kr_virtual_position(recommendation, armed, (old,), old.observed_at)
    censored = advance_kr_virtual_position(recommendation, armed, (gap,), gap.observed_at)

    # Then: the old bar creates no fill and a missing minute fabricates no outcome price.
    assert ignored[-1].state is KrVirtualPositionState.EXPIRED
    assert ignored[-1].fill_price is None and ignored[-1].exit_price is None
    assert censored[-1].state is KrVirtualPositionState.CENSORED
    assert censored[-1].reason is KrVirtualPositionReason.BAR_GAP
    assert censored[-1].fill_price is None and censored[-1].exit_price is None


def test_model_failure_blocks_without_any_tool_or_provider_call(tmp_path: Path) -> None:
    # Given: a durable task and a fixture model process with no valid response.
    runtime = _runtime(tmp_path, fixture_reasoner(tmp_path, ()))
    task = task_fixture()
    with AutonomousTaskStore(runtime.tasks.path).writer() as writer:
        assert writer.create_task(task)

    # When: the supervisor requests its next autonomous decision.
    result = runtime.tick(task, SUPERVISOR_NOW)

    # Then: model failure becomes a blocked wait before any tool can run.
    assert result.status == "blocked"
    assert result.tool_calls == 0

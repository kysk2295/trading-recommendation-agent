from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from tests.day_research_review_support import (
    authority_event,
    content_id,
    promotion_payload,
    session_context,
)
from tests.test_day_historical_evidence import NOW
from trading_agent.day_research_ledger import InvalidDayResearchLedgerSourceError
from trading_agent.day_research_ledger_reader import (
    day_execution_eligibility_events,
    day_promotion_decisions,
)
from trading_agent.day_research_ledger_schema import CREATE_DAY_RESEARCH_LEDGER_SCHEMA_V10
from trading_agent.day_research_review import (
    append_execution_eligibility,
    build_execution_eligibility,
    build_review_feedback,
    record_promotion_decision,
    seal_owner_authority_event,
    seal_promotion_decision,
)
from trading_agent.day_research_review_models import (
    DayExecutionAuthorityClass,
    DayExecutionEligibilityStatus,
    DayOwnerAuthorityEventPayload,
    ExecutionEligibility,
    ExecutionEligibilityPayload,
)
from trading_agent.intraday_promotion_models import DayPromotionStatus
from trading_agent.research_identity_models import MarketId


def test_builders_seal_decision_and_owner_event_identities() -> None:
    # Given: validated promotion and owner authority payloads.
    review_payload = promotion_payload()
    decision = seal_promotion_decision(review_payload)
    authority_payload = DayOwnerAuthorityEventPayload(
        decision_id=decision.decision_id,
        capsule_id=decision.payload.capsule_id,
        hypothesis_version_id=decision.payload.hypothesis_version_id,
        market_id=decision.payload.market_id,
        authority_class=DayExecutionAuthorityClass.PAPER_TRIAL_APPROVED,
        owner_id="owner_1",
        approved_at=NOW + dt.timedelta(minutes=1),
        effective_after_session=decision.payload.effective_after_session,
    )

    # When: the only supported seal builders are used.
    authority = seal_owner_authority_event(authority_payload)

    # Then: both immutable IDs bind their complete payloads.
    assert decision.decision_id == content_id(review_payload)
    assert authority.authority_event_id == content_id(authority_payload)


def test_execution_builder_blocks_us_candidate_until_owner_approval() -> None:
    # Given: a US Paper trial candidate without an owner authority event.
    decision = seal_promotion_decision(promotion_payload())

    # When: session eligibility is evaluated.
    eligibility = build_execution_eligibility(decision, session_context(decision))

    # Then: promotion remains blocked from orders pending owner approval.
    assert eligibility.payload.status is DayExecutionEligibilityStatus.BLOCKED
    assert eligibility.payload.blockers == ("owner_approval_required",)
    assert eligibility.payload.paper_order_authority is False


def test_execution_builder_grants_us_session_only_for_matching_owner_class() -> None:
    # Given: a US Paper trial candidate with its exact owner authority event.
    decision = seal_promotion_decision(promotion_payload())
    authority = authority_event(decision)

    # When: session eligibility is evaluated with that event.
    eligibility = build_execution_eligibility(
        decision,
        session_context(decision),
        authority,
    )

    # Then: only this expiring session artifact carries Paper order authority.
    assert eligibility.payload.status is DayExecutionEligibilityStatus.ELIGIBLE
    assert eligibility.payload.paper_order_authority is True
    assert eligibility.payload.authority_event == authority


def test_execution_builder_keeps_kr_provider_read_only() -> None:
    # Given: a Korean Shadow candidate.
    decision = seal_promotion_decision(
        promotion_payload(
            market_id=MarketId.KR_EQUITIES,
            status=DayPromotionStatus.SHADOW_CANDIDATE,
        )
    )

    # When: its session eligibility is evaluated.
    eligibility = build_execution_eligibility(decision, session_context(decision))

    # Then: the artifact records the provider read-only broker block.
    assert eligibility.payload.status is DayExecutionEligibilityStatus.BLOCKED
    assert eligibility.payload.broker_blocked is True
    assert eligibility.payload.blockers == ("provider_read_only",)


def test_feedback_builder_copies_only_sanitized_review_fields() -> None:
    # Given: a sealed US promotion decision with exact selection statistics.
    decision = seal_promotion_decision(promotion_payload())

    # When: generator-facing feedback is built.
    feedback = build_review_feedback(
        decision,
        dt.date(2026, 8, 21),
        ("review_passed",),
    )

    # Then: no exact statistic or provider detail is serialized.
    serialized = feedback.model_dump_json()
    assert feedback.decision_id == decision.decision_id
    assert all(token not in serialized for token in ("0.91", "0.12", "account", "authorization"))


def test_review_and_session_eligibility_round_trip_across_restart(tmp_path: Path) -> None:
    # Given: a Day ledger with one exact US capsule lineage and owner-approved session.
    database = tmp_path / "day-review.sqlite3"
    decision = seal_promotion_decision(promotion_payload())
    authority = authority_event(decision)
    eligibility = build_execution_eligibility(
        decision,
        session_context(decision),
        authority,
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(CREATE_DAY_RESEARCH_LEDGER_SCHEMA_V10)
        connection.execute(
            "INSERT INTO day_strategy_capsules VALUES (?,?,?,?,?)",
            (
                decision.payload.capsule_id,
                decision.payload.hypothesis_version_id,
                decision.payload.market_id.value,
                NOW.isoformat(),
                "{}",
            ),
        )

        # When: decision and eligibility are appended, including idempotent replay.
        assert record_promotion_decision(connection, decision) is True
        assert record_promotion_decision(connection, decision) is False
        assert append_execution_eligibility(connection, eligibility) is True
        assert append_execution_eligibility(connection, eligibility) is False

    with sqlite3.connect(database) as restarted:
        decisions = day_promotion_decisions(restarted)
        events = day_execution_eligibility_events(restarted)

    # Then: restart-safe projections preserve exact evidence and authority separation.
    assert decisions == (decision,)
    assert events == (eligibility,)
    assert decisions[0].payload.paper_order_authority is False
    assert events[0].payload.paper_order_authority is True


def test_ledger_rejects_owner_authority_for_the_wrong_paper_class(tmp_path: Path) -> None:
    # Given: a champion decision paired with a trial-only owner authority event.
    database = tmp_path / "wrong-authority.sqlite3"
    decision = seal_promotion_decision(promotion_payload(status=DayPromotionStatus.PAPER_CHAMPION_CANDIDATE))
    trial_payload = DayOwnerAuthorityEventPayload(
        decision_id=decision.decision_id,
        capsule_id=decision.payload.capsule_id,
        hypothesis_version_id=decision.payload.hypothesis_version_id,
        market_id=decision.payload.market_id,
        authority_class=DayExecutionAuthorityClass.PAPER_TRIAL_APPROVED,
        owner_id="owner_1",
        approved_at=NOW + dt.timedelta(minutes=1),
        effective_after_session=decision.payload.effective_after_session,
    )
    trial_authority = seal_owner_authority_event(trial_payload)
    context = session_context(decision)
    eligibility_payload = ExecutionEligibilityPayload(
        decision_id=decision.decision_id,
        capsule_id=decision.payload.capsule_id,
        hypothesis_version_id=decision.payload.hypothesis_version_id,
        market_id=decision.payload.market_id,
        session_date=context.session_date,
        sequence=context.sequence,
        previous_event_id=context.previous_event_id,
        clean_commit_sha256=context.clean_commit_sha256,
        risk_policy_sha256=context.risk_policy_sha256,
        authority_event=trial_authority,
        effective_at=context.effective_at,
        expires_at=context.expires_at,
        status=DayExecutionEligibilityStatus.ELIGIBLE,
        broker_blocked=False,
        blockers=(),
        paper_order_authority=True,
    )
    eligibility = ExecutionEligibility(
        eligibility_event_id=content_id(eligibility_payload),
        payload=eligibility_payload,
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(CREATE_DAY_RESEARCH_LEDGER_SCHEMA_V10)
        connection.execute(
            "INSERT INTO day_strategy_capsules VALUES (?,?,?,?,?)",
            (
                decision.payload.capsule_id,
                decision.payload.hypothesis_version_id,
                decision.payload.market_id.value,
                NOW.isoformat(),
                "{}",
            ),
        )
        record_promotion_decision(connection, decision)

        # When / Then: the append boundary rejects the mismatched Paper authority class.
        with pytest.raises(InvalidDayResearchLedgerSourceError, match="authority_class"):
            append_execution_eligibility(connection, eligibility)

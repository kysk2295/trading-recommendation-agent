from __future__ import annotations

import datetime as dt
import importlib
import sqlite3
from importlib.util import find_spec

import pytest
from pydantic import ValidationError

from tests.test_day_learning_report_models import NOW, SHA_A, SHA_B, _payload, _report
from trading_agent.day_learning_policy import (
    ExplorationPolicyAction,
    ExplorationPolicyPayload,
    ExplorationPolicyRequest,
    OfficialNextSessionCalendarSnapshot,
    build_exploration_policy,
)
from trading_agent.day_learning_report_models import MarketCloseReportPayload, NextSessionSection
from trading_agent.day_research_ledger import (
    DayResearchLedgerConflictError,
    record_day_exploration_policy,
)
from trading_agent.day_research_ledger_reader import day_exploration_policies
from trading_agent.day_research_ledger_schema import CREATE_DAY_RESEARCH_LEDGER_SCHEMA_V10
from trading_agent.day_research_review_models import ReviewFeedbackSummary
from trading_agent.intraday_overfit_diagnostics_models import IntradayOverfitDiagnosticsStatus
from trading_agent.intraday_promotion_models import DayPromotionStatus
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_types import TerminalOutcome


def test_day_learning_policy_module_exists() -> None:
    # Given: finalized market-close report contracts.
    module_name = "trading_agent.day_learning_policy"

    # When: the next-session policy boundary is resolved.
    module = find_spec(module_name)

    # Then: future research activation has a dedicated module.
    assert module is not None


def test_day_learning_policy_api_is_explicit() -> None:
    # Given: the dedicated next-session policy module.
    module = importlib.import_module("trading_agent.day_learning_policy")

    # When: its public activation contracts are inspected.
    names = {
        "ExplorationPolicy",
        "ExplorationPolicyAction",
        "ExplorationPolicyPayload",
        "ExplorationPolicyRequest",
        "OfficialNextSessionCalendarSnapshot",
        "build_exploration_policy",
    }

    # Then: official-calendar policy construction is a closed public surface.
    assert names <= set(module.__all__)


def _calendar(
    market_id: MarketId = MarketId.US_EQUITIES,
) -> OfficialNextSessionCalendarSnapshot:
    exchange = "XNYS" if market_id is MarketId.US_EQUITIES else "XKRX"
    return OfficialNextSessionCalendarSnapshot(
        calendar_snapshot_id=f"calendar://official/{exchange}/2026-08-21-v1",
        market_id=market_id,
        report_session_date=dt.date(2026, 8, 20),
        effective_session_date=dt.date(2026, 8, 21),
        observed_at=NOW,
    )


def _feedback(
    capsule_id: str,
    market_id: MarketId = MarketId.US_EQUITIES,
) -> ReviewFeedbackSummary:
    return ReviewFeedbackSummary(
        decision_id=capsule_id,
        capsule_id=capsule_id,
        market_id=market_id,
        status=DayPromotionStatus.SHADOW_CANDIDATE,
        classification=TerminalOutcome.SUPPORTED,
        reason_codes=("review_passed",),
        selection_diagnostics_status=IntradayOverfitDiagnosticsStatus.DIAGNOSTIC_READY,
        power_ci_sufficient=True,
        next_review_date=dt.date(2026, 8, 21),
    )


def test_policy_requires_official_later_market_session_snapshot() -> None:
    # Given: a next-session snapshot that reuses the report session date.
    payload = _calendar().model_dump(mode="python")

    # When / Then: the policy calendar boundary rejects a non-future session.
    with pytest.raises(ValidationError, match="calendar"):
        _ = OfficialNextSessionCalendarSnapshot.model_validate(
            payload | {"effective_session_date": dt.date(2026, 8, 20)}
        )


@pytest.mark.parametrize(
    "calendar_snapshot_id",
    (
        "calendar://cached/XNYS/2026-08-21-v1",
        "calendar://official/XKRX/2026-08-21-v1",
    ),
)
def test_policy_rejects_nonofficial_or_wrong_market_calendar(
    calendar_snapshot_id: str,
) -> None:
    # Given: an untrusted or market-mismatched calendar identity.
    payload = _calendar().model_dump(mode="python")

    # When / Then: only the official exchange calendar for the report market is accepted.
    with pytest.raises(ValidationError, match="calendar"):
        _ = OfficialNextSessionCalendarSnapshot.model_validate(payload | {"calendar_snapshot_id": calendar_snapshot_id})


def test_policy_payload_cannot_bypass_the_official_calendar_boundary() -> None:
    # Given: a built policy whose stored payload is changed to an untrusted calendar ID.
    report = _report(_payload())
    policy = build_exploration_policy(
        ExplorationPolicyRequest(
            latest_final_report=report,
            feedback=(_feedback(SHA_A),),
            calendar=_calendar(),
            action=ExplorationPolicyAction.KEEP,
            effective_at=NOW + dt.timedelta(minutes=1),
        )
    )
    raw = policy.payload.model_dump(mode="python")

    # When / Then: direct payload construction fails before ledger persistence.
    with pytest.raises(ValidationError, match="payload"):
        _ = ExplorationPolicyPayload.model_validate(
            raw | {"calendar_snapshot_id": "calendar://cached/XNYS/2026-08-21-v1"}
        )


def test_policy_is_deterministic_bounded_and_cannot_change_authority() -> None:
    # Given: a final report with three active and two queued capsules.
    capsule_ids = (SHA_A, SHA_B, "c" * 64, "d" * 64, "e" * 64)
    report_payload = _payload().model_dump(mode="python")
    next_session = NextSessionSection(
        market_id=MarketId.US_EQUITIES,
        active_capsule_ids=capsule_ids[:3],
        queued_capsule_ids=capsule_ids[3:],
        reason_codes=("keep_supported_capsule",),
    )
    report = _report(MarketCloseReportPayload.model_validate(report_payload | {"next_session": next_session}))
    request = ExplorationPolicyRequest(
        latest_final_report=report,
        feedback=tuple(_feedback(capsule_id) for capsule_id in reversed(capsule_ids)),
        calendar=_calendar(),
        action=ExplorationPolicyAction.KEEP,
        effective_at=NOW + dt.timedelta(minutes=1),
    )

    # When: the next-session exploration policy is built.
    policy = build_exploration_policy(request)

    # Then: three slots remain active, the queue is canonical, and authority fields do not exist.
    assert policy.payload.active_capsule_ids == capsule_ids[:3]
    assert policy.payload.queued_capsule_ids == capsule_ids[3:]
    assert policy.payload.final_report_id == report.report_id
    fields = set(type(policy.payload).model_fields)
    assert (
        not {
            "risk_policy_sha256",
            "strategy_source",
            "promotion_status",
            "execution_eligibility",
            "order_authority",
        }
        & fields
    )


@pytest.mark.parametrize(
    ("action", "expected_active", "expected_queue"),
    (
        (
            ExplorationPolicyAction.ROTATE_EXPLORATION,
            (SHA_B, "c" * 64, "d" * 64),
            (SHA_A, "e" * 64),
        ),
        (
            ExplorationPolicyAction.SUSPEND_SHADOW,
            (),
            (SHA_A, SHA_B, "c" * 64, "d" * 64, "e" * 64),
        ),
        (
            ExplorationPolicyAction.NO_TRADE,
            (),
            (SHA_A, SHA_B, "c" * 64, "d" * 64, "e" * 64),
        ),
    ),
)
def test_policy_actions_apply_a_deterministic_slot_transition(
    action: ExplorationPolicyAction,
    expected_active: tuple[str, ...],
    expected_queue: tuple[str, ...],
) -> None:
    # Given: a full active set and a deterministic exploration queue.
    capsule_ids = (SHA_A, SHA_B, "c" * 64, "d" * 64, "e" * 64)
    raw = _payload().model_dump(mode="python")
    report = _report(
        MarketCloseReportPayload.model_validate(
            raw
            | {
                "next_session": NextSessionSection(
                    market_id=MarketId.US_EQUITIES,
                    active_capsule_ids=capsule_ids[:3],
                    queued_capsule_ids=capsule_ids[3:],
                    reason_codes=("deterministic_rotation",),
                )
            }
        )
    )
    request = ExplorationPolicyRequest(
        latest_final_report=report,
        feedback=tuple(_feedback(capsule_id) for capsule_id in capsule_ids),
        calendar=_calendar(),
        action=action,
        effective_at=NOW + dt.timedelta(minutes=1),
    )

    # When: a non-KEEP action is applied.
    policy = build_exploration_policy(request)

    # Then: its bounded transition is stable and loses no candidate identity.
    assert policy.payload.active_capsule_ids == expected_active
    assert policy.payload.queued_capsule_ids == expected_queue
    assert set(expected_active) | set(expected_queue) == set(capsule_ids)


def test_policy_rejects_cross_market_redacted_feedback() -> None:
    # Given: US report inputs containing one feedback item labeled as Korean-market evidence.
    feedback = _feedback(SHA_A).model_dump(mode="python")

    # When / Then: the request boundary rejects feedback leakage across market partitions.
    with pytest.raises(ValidationError, match="request"):
        _ = ExplorationPolicyRequest(
            latest_final_report=_report(_payload()),
            feedback=(ReviewFeedbackSummary.model_validate(feedback | {"market_id": MarketId.KR_EQUITIES}),),
            calendar=_calendar(),
            action=ExplorationPolicyAction.KEEP,
            effective_at=NOW + dt.timedelta(minutes=1),
        )


def test_korean_policy_uses_the_xkrx_calendar_and_remains_research_only() -> None:
    # Given: a Korean read-only final report and its official next-session calendar.
    report = _report(_payload(MarketId.KR_EQUITIES))
    request = ExplorationPolicyRequest(
        latest_final_report=report,
        feedback=(_feedback(SHA_A, MarketId.KR_EQUITIES),),
        calendar=_calendar(MarketId.KR_EQUITIES),
        action=ExplorationPolicyAction.KEEP,
        effective_at=NOW + dt.timedelta(minutes=1),
    )

    # When: the next Korean research activation is built.
    policy = build_exploration_policy(request)

    # Then: it is market-scoped and contains no execution authority surface.
    assert policy.payload.market_id is MarketId.KR_EQUITIES
    assert policy.payload.calendar_snapshot_id.startswith("calendar://official/XKRX/")
    assert all("authority" not in field for field in type(policy.payload).model_fields)


def test_policy_ledger_is_idempotent_and_stores_no_raw_report_metrics() -> None:
    # Given: a deterministic policy based on one exact final report revision.
    report = _report(_payload())
    request = ExplorationPolicyRequest(
        latest_final_report=report,
        feedback=(_feedback(SHA_A),),
        calendar=_calendar(),
        action=ExplorationPolicyAction.KEEP,
        effective_at=NOW + dt.timedelta(minutes=1),
    )
    policy = build_exploration_policy(request)
    connection = sqlite3.connect(":memory:")
    connection.executescript(CREATE_DAY_RESEARCH_LEDGER_SCHEMA_V10)

    # When: the same policy is recorded twice and read through the ledger projection.
    first = record_day_exploration_policy(connection, policy)
    replay = record_day_exploration_policy(connection, policy)
    stored = day_exploration_policies(connection, MarketId.US_EQUITIES)
    raw_payload = connection.execute(
        "SELECT payload_json FROM day_exploration_policies WHERE policy_id=?",
        (policy.policy_id,),
    ).fetchone()[0]

    # Then: replay is a no-op and only activation IDs, not report metrics, persist.
    assert (first, replay, stored) == (True, False, (policy,))
    assert report.report_id in raw_payload
    assert not {
        "actual_return",
        "modeled_return",
        "filled_order_count",
        "unresolved_count",
        "censored_count",
    } & set(type(policy.payload).model_fields)
    assert "actual_return" not in raw_payload
    connection.close()


def test_policy_ledger_rejects_two_policies_for_one_market_session() -> None:
    # Given: one recorded activation policy for a market session.
    report = _report(_payload())
    base = {
        "latest_final_report": report,
        "feedback": (_feedback(SHA_A),),
        "calendar": _calendar(),
        "effective_at": NOW + dt.timedelta(minutes=1),
    }
    first = build_exploration_policy(ExplorationPolicyRequest(**base, action=ExplorationPolicyAction.KEEP))
    conflicting = build_exploration_policy(ExplorationPolicyRequest(**base, action=ExplorationPolicyAction.NO_TRADE))
    connection = sqlite3.connect(":memory:")
    connection.executescript(CREATE_DAY_RESEARCH_LEDGER_SCHEMA_V10)
    _ = record_day_exploration_policy(connection, first)

    # When / Then: a different policy cannot silently replace that session's decision.
    with pytest.raises(DayResearchLedgerConflictError):
        _ = record_day_exploration_policy(connection, conflicting)
    connection.close()

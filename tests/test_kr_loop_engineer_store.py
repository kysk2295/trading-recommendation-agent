from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.kr_loop_engineer_models import (
    KrLoopCandidateState,
    KrLoopHealthReceipt,
    KrLoopReleaseAction,
    KrLoopShadowReceipt,
    build_candidate_snapshot,
    build_release_event,
)
from trading_agent.kr_loop_engineer_store import InvalidKrLoopEngineerStoreError, KrLoopEngineerStore

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 8, 27, 10, 0, tzinfo=KST)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
PATHS = ("trading_agent/kr_social_signal_models.py", "tests/test_kr_social_signal.py")


def test_snapshot_replay_is_idempotent_and_transition_history_is_append_only(tmp_path: Path) -> None:
    # Given: a detected candidate bound to one immutable failure bundle.
    detected = build_candidate_snapshot(
        bundle_id=SHA_A,
        base_commit=SHA_B,
        allowed_paths=PATHS,
        state=KrLoopCandidateState.DETECTED,
        updated_at=NOW,
    )
    store = KrLoopEngineerStore(tmp_path / "loop.sqlite3")

    # When: the exact snapshot is appended twice, followed by a valid candidate snapshot.
    assert store.append(detected)
    assert not store.append(detected)
    ready = build_candidate_snapshot(
        bundle_id=SHA_A,
        base_commit=SHA_B,
        allowed_paths=PATHS,
        state=KrLoopCandidateState.CANDIDATE_READY,
        updated_at=NOW + dt.timedelta(minutes=1),
        previous=detected,
        candidate_commit=SHA_C,
        patch_sha256="d" * 64,
    )
    assert store.append(ready)

    # Then: both immutable states remain readable in their exact order.
    assert store.latest(detected.candidate_id) == ready
    assert store.history(detected.candidate_id) == (detected, ready)
    assert detected.snapshot_id != ready.snapshot_id


def test_store_rejects_a_snapshot_that_skips_the_candidate_chain(tmp_path: Path) -> None:
    # Given: a store whose candidate chain already has a detected tail.
    detected = build_candidate_snapshot(
        bundle_id=SHA_A,
        base_commit=SHA_B,
        allowed_paths=PATHS,
        state=KrLoopCandidateState.DETECTED,
        updated_at=NOW,
    )
    store = KrLoopEngineerStore(tmp_path / "loop.sqlite3")
    assert store.append(detected)
    stale = build_candidate_snapshot(
        bundle_id=SHA_A,
        base_commit=SHA_B,
        allowed_paths=PATHS,
        state=KrLoopCandidateState.REJECTED,
        updated_at=NOW + dt.timedelta(minutes=1),
        previous=None,
        reason_codes=("validation_failed",),
    )

    # When / Then: a second root snapshot cannot overwrite or fork the chain.
    with pytest.raises(InvalidKrLoopEngineerStoreError):
        store.append(stale)


def test_promotion_and_rollback_change_release_generation_atomically(tmp_path: Path) -> None:
    # Given: a challenger with two clean future-shadow sessions and a promoted snapshot.
    store = KrLoopEngineerStore(tmp_path / "loop.sqlite3")
    detected = build_candidate_snapshot(
        bundle_id=SHA_A,
        base_commit=SHA_B,
        allowed_paths=PATHS,
        state=KrLoopCandidateState.DETECTED,
        updated_at=NOW,
    )
    assert store.append(detected)
    ready = build_candidate_snapshot(
        bundle_id=SHA_A,
        base_commit=SHA_B,
        allowed_paths=PATHS,
        state=KrLoopCandidateState.CANDIDATE_READY,
        updated_at=NOW + dt.timedelta(minutes=1),
        previous=detected,
        candidate_commit=SHA_C,
        patch_sha256="d" * 64,
    )
    assert store.append(ready)
    shadowing = build_candidate_snapshot(
        bundle_id=SHA_A,
        base_commit=SHA_B,
        allowed_paths=PATHS,
        state=KrLoopCandidateState.SHADOWING,
        updated_at=NOW + dt.timedelta(minutes=2),
        previous=ready,
        candidate_commit=SHA_C,
        patch_sha256="d" * 64,
        verification_sha256="e" * 64,
    )
    assert store.append(shadowing)
    promoted = build_candidate_snapshot(
        bundle_id=SHA_A,
        base_commit=SHA_B,
        allowed_paths=PATHS,
        state=KrLoopCandidateState.PROMOTED,
        updated_at=NOW + dt.timedelta(days=2),
        previous=shadowing,
        candidate_commit=SHA_C,
        patch_sha256="d" * 64,
        verification_sha256="e" * 64,
        shadow_receipts=(_shadow(1), _shadow(2)),
    )
    promotion = build_release_event(
        action=KrLoopReleaseAction.PROMOTE,
        candidate=promoted,
        previous=None,
        recorded_at=promoted.updated_at,
    )

    # When: promotion and a later host-health rollback are committed.
    assert store.append_release(promoted, promotion)
    health = KrLoopHealthReceipt(
        release_id=promotion.release_id,
        observed_at=NOW + dt.timedelta(days=3),
        error_rate=Decimal("0.06"),
        data_eligibility_failures=0,
        order_mismatches=0,
        research_task_losses=0,
        evidence_refs=("health:1",),
    )
    rolled_back = build_candidate_snapshot(
        bundle_id=SHA_A,
        base_commit=SHA_B,
        allowed_paths=PATHS,
        state=KrLoopCandidateState.ROLLED_BACK,
        updated_at=health.observed_at,
        previous=promoted,
        candidate_commit=SHA_C,
        patch_sha256="d" * 64,
        verification_sha256=promoted.verification_sha256,
        shadow_receipts=promoted.shadow_receipts,
        health_receipts=(health,),
        reason_codes=("error_rate_threshold",),
    )
    rollback = build_release_event(
        action=KrLoopReleaseAction.ROLLBACK,
        candidate=rolled_back,
        previous=promotion,
        recorded_at=health.observed_at,
    )
    assert store.append_release(rolled_back, rollback)

    # Then: the release generation advances and the active commit is restored to the base.
    assert store.releases() == (promotion, rollback)
    assert rollback.generation == 2
    assert rollback.active_commit == SHA_B
    assert rollback.previous_commit == SHA_C
    assert store.latest(rolled_back.candidate_id) == rolled_back


def _shadow(day: int) -> KrLoopShadowReceipt:
    return KrLoopShadowReceipt(
        session_date=dt.date(2026, 8, 27) + dt.timedelta(days=day),
        observed_at=NOW + dt.timedelta(days=day),
        champion_score=Decimal("0.40"),
        challenger_score=Decimal("0.55"),
        error_count=0,
        data_eligibility_failures=0,
        order_mismatches=0,
        research_task_losses=0,
        evidence_refs=(f"shadow:{day}",),
    )

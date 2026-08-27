from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from trading_agent.kr_autonomous_outcome_models import (
    KrLoopEngineerEvidenceBundle,
    KrLoopFailureCode,
    kr_loop_engineer_bundle_id,
)
from trading_agent.kr_loop_engineer_controller import KrLoopEngineerController
from trading_agent.kr_loop_engineer_models import (
    KrLoopCandidateSnapshot,
    KrLoopCandidateState,
    KrLoopHealthReceipt,
    KrLoopShadowReceipt,
    KrLoopValidationReceipt,
    build_candidate_snapshot,
)
from trading_agent.kr_loop_engineer_mutation import KrLoopMutationResult, KrLoopMutationStatus
from trading_agent.kr_loop_engineer_store import KrLoopEngineerStore

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 8, 27, 18, 0, tzinfo=KST)
BASE = "a" * 40
COMMIT = "b" * 40


@dataclass(frozen=True, slots=True)
class _SuccessfulMutation:
    def execute(
        self,
        bundle: KrLoopEngineerEvidenceBundle,
        *,
        base_commit: str,
        now: dt.datetime,
        previous: KrLoopCandidateSnapshot | None = None,
    ) -> KrLoopMutationResult:
        assert previous is not None
        ready = build_candidate_snapshot(
            bundle_id=bundle.bundle_id,
            base_commit=base_commit,
            allowed_paths=previous.allowed_paths,
            state=KrLoopCandidateState.CANDIDATE_READY,
            updated_at=now,
            previous=previous,
            candidate_commit=COMMIT,
            patch_sha256="c" * 64,
        )
        return KrLoopMutationResult(
            status=KrLoopMutationStatus.COMPLETED,
            snapshot=ready,
            changed_paths=(previous.allowed_paths[0],),
            reason_code=None,
        )


def test_bundle_ingestion_and_mutation_are_restart_idempotent(tmp_path: Path) -> None:
    # Given: a durable repeated-failure bundle and an empty controller ledger.
    store = KrLoopEngineerStore(tmp_path / "loop.sqlite3")
    controller = KrLoopEngineerController(store, _SuccessfulMutation())
    bundle = _bundle()

    # When: ingestion replays, mutation runs once, and the process is reconstructed.
    detected = controller.ingest(bundle, base_commit=BASE, now=NOW)
    assert controller.ingest(bundle, base_commit=BASE, now=NOW) == detected
    ready = controller.mutate(bundle, now=NOW + dt.timedelta(minutes=1))
    restarted = KrLoopEngineerController(KrLoopEngineerStore(store.path), _SuccessfulMutation())
    replay = restarted.mutate(bundle, now=NOW + dt.timedelta(minutes=2))

    # Then: one candidate-ready snapshot is preserved without a duplicate mutation.
    assert ready.state is KrLoopCandidateState.CANDIDATE_READY
    assert replay == ready
    assert tuple(item.state for item in store.history(ready.candidate_id)) == (
        KrLoopCandidateState.DETECTED,
        KrLoopCandidateState.CANDIDATE_READY,
    )


def test_validation_and_two_distinct_future_sessions_are_required_for_promotion(tmp_path: Path) -> None:
    # Given: a mutated candidate with a passing independent validation receipt.
    store = KrLoopEngineerStore(tmp_path / "loop.sqlite3")
    controller = KrLoopEngineerController(store, _SuccessfulMutation())
    bundle = _bundle()
    _ = controller.ingest(bundle, base_commit=BASE, now=NOW)
    ready = controller.mutate(bundle, now=NOW + dt.timedelta(minutes=1))
    shadowing = controller.validate(ready.candidate_id, _validation(ready))

    # When: one superior future shadow session arrives, followed by a second session.
    first = controller.record_shadow(shadowing.candidate_id, _shadow(1))
    promoted = controller.record_shadow(shadowing.candidate_id, _shadow(2))

    # Then: one session remains shadow-only and the second atomically promotes a paper-only release.
    assert first.state is KrLoopCandidateState.SHADOWING
    assert promoted.state is KrLoopCandidateState.PROMOTED
    assert promoted.trading_authority is False
    releases = store.releases()
    assert len(releases) == 1
    assert releases[0].active_commit == COMMIT


def test_host_health_breach_restores_previous_release_once(tmp_path: Path) -> None:
    # Given: a promoted candidate release.
    store = KrLoopEngineerStore(tmp_path / "loop.sqlite3")
    controller = KrLoopEngineerController(store, _SuccessfulMutation())
    bundle = _bundle()
    _ = controller.ingest(bundle, base_commit=BASE, now=NOW)
    ready = controller.mutate(bundle, now=NOW + dt.timedelta(minutes=1))
    shadowing = controller.validate(ready.candidate_id, _validation(ready))
    _ = controller.record_shadow(shadowing.candidate_id, _shadow(1))
    promoted = controller.record_shadow(shadowing.candidate_id, _shadow(2))
    release = store.releases()[0]
    health = KrLoopHealthReceipt(
        release_id=release.release_id,
        observed_at=NOW + dt.timedelta(days=3),
        error_rate=Decimal("0.00"),
        data_eligibility_failures=0,
        order_mismatches=1,
        research_task_losses=0,
        evidence_refs=("health:order-mismatch",),
    )

    # When: the health receipt and its exact replay are processed.
    rolled_back = controller.record_health(health)
    replay = controller.record_health(health)

    # Then: rollback advances the release once and restores the immutable base commit.
    assert rolled_back.state is KrLoopCandidateState.ROLLED_BACK
    assert replay == rolled_back
    assert promoted.snapshot_id == rolled_back.previous_snapshot_id
    assert len(store.releases()) == 2
    assert store.releases()[-1].active_commit == BASE
    assert store.releases()[-1].previous_commit == COMMIT


def _validation(candidate: KrLoopCandidateSnapshot) -> KrLoopValidationReceipt:
    assert candidate.candidate_commit is not None
    return KrLoopValidationReceipt.build(
        candidate_id=candidate.candidate_id,
        candidate_commit=candidate.candidate_commit,
        verified_at=NOW + dt.timedelta(minutes=2),
        pytest_passed=True,
        ruff_passed=True,
        basedpyright_passed=True,
        manual_qa_passed=True,
        replay_passed=True,
        lookahead_violations=0,
        broker_mutations=0,
        evidence_refs=("validation:fixture-replay",),
    )


def _shadow(day: int) -> KrLoopShadowReceipt:
    return KrLoopShadowReceipt(
        session_date=NOW.date() + dt.timedelta(days=day),
        observed_at=NOW + dt.timedelta(days=day),
        champion_score=Decimal("0.40"),
        challenger_score=Decimal("0.50"),
        error_count=0,
        data_eligibility_failures=0,
        order_mismatches=0,
        research_task_losses=0,
        evidence_refs=(f"shadow:{day}",),
    )


def _bundle() -> KrLoopEngineerEvidenceBundle:
    draft = KrLoopEngineerEvidenceBundle.model_construct(
        bundle_id="",
        failure_code=KrLoopFailureCode.CRITIC_CLUSTER_COUNT,
        subject_ref="symbol:005930",
        source_memory_ids=("1" * 64, "2" * 64, "3" * 64),
        source_task_ids=("4" * 64, "5" * 64, "6" * 64),
        evidence_refs=("evidence:1",),
        change_hypothesis="Tighten independent-source clustering with deterministic replay evidence.",
        created_at=NOW,
    )
    return KrLoopEngineerEvidenceBundle.model_validate(
        draft.model_copy(update={"bundle_id": kr_loop_engineer_bundle_id(draft)}).model_dump(mode="python")
    )

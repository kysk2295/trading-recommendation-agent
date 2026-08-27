from __future__ import annotations

import datetime as dt
from typing import Protocol, assert_never, override

from trading_agent.kr_autonomous_outcome_models import KrLoopEngineerEvidenceBundle
from trading_agent.kr_loop_engineer_gates import health_reasons, promotion_blocked, validation_reasons
from trading_agent.kr_loop_engineer_models import (
    KrLoopCandidateSnapshot,
    KrLoopCandidateState,
    KrLoopHealthReceipt,
    KrLoopReleaseAction,
    KrLoopShadowReceipt,
    KrLoopValidationReceipt,
    build_candidate_snapshot,
    build_release_event,
)
from trading_agent.kr_loop_engineer_mutation import KrLoopMutationResult, KrLoopMutationStatus
from trading_agent.kr_loop_engineer_policy import mutation_contract
from trading_agent.kr_loop_engineer_store import KrLoopEngineerStore


class KrLoopMutationService(Protocol):
    def execute(
        self,
        bundle: KrLoopEngineerEvidenceBundle,
        *,
        base_commit: str,
        now: dt.datetime,
        previous: KrLoopCandidateSnapshot | None = None,
    ) -> KrLoopMutationResult: ...


class InvalidKrLoopEngineerControllerError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR Loop Engineer controller input is invalid"


class KrLoopEngineerController:
    __slots__ = ("_mutation", "store")

    def __init__(self, store: KrLoopEngineerStore, mutation: KrLoopMutationService) -> None:
        self.store = store
        self._mutation = mutation

    def ingest(
        self,
        bundle: KrLoopEngineerEvidenceBundle,
        *,
        base_commit: str,
        now: dt.datetime,
    ) -> KrLoopCandidateSnapshot:
        contract = mutation_contract(bundle, base_commit)
        detected = build_candidate_snapshot(
            bundle_id=bundle.bundle_id,
            base_commit=base_commit,
            allowed_paths=contract.allowed_paths,
            state=KrLoopCandidateState.DETECTED,
            updated_at=now,
        )
        existing = self.store.latest(detected.candidate_id)
        if existing is not None:
            return existing
        releases = self.store.releases()
        if releases and releases[-1].active_commit != base_commit:
            raise InvalidKrLoopEngineerControllerError
        _ = self.store.append(detected)
        return detected

    def mutate(
        self,
        bundle: KrLoopEngineerEvidenceBundle,
        *,
        now: dt.datetime,
    ) -> KrLoopCandidateSnapshot:
        candidates = tuple(item for item in self.store.snapshots() if item.bundle_id == bundle.bundle_id)
        if not candidates:
            raise InvalidKrLoopEngineerControllerError
        latest = candidates[-1]
        if latest.state is not KrLoopCandidateState.DETECTED:
            return latest
        result = self._mutation.execute(
            bundle,
            base_commit=latest.base_commit,
            now=now,
            previous=latest,
        )
        match result.status:
            case KrLoopMutationStatus.COMPLETED:
                if result.snapshot is None:
                    raise InvalidKrLoopEngineerControllerError
                next_snapshot = result.snapshot
            case KrLoopMutationStatus.REJECTED:
                next_snapshot = build_candidate_snapshot(
                    bundle_id=latest.bundle_id,
                    base_commit=latest.base_commit,
                    allowed_paths=latest.allowed_paths,
                    state=KrLoopCandidateState.REJECTED,
                    updated_at=now,
                    previous=latest,
                    reason_codes=(result.reason_code or "mutation_failed",),
                )
            case unreachable:
                assert_never(unreachable)
        _ = self.store.append(next_snapshot)
        if result.validation_receipt is not None:
            return self.validate(next_snapshot.candidate_id, result.validation_receipt)
        return next_snapshot

    def validate(
        self,
        candidate_id: str,
        receipt: KrLoopValidationReceipt,
    ) -> KrLoopCandidateSnapshot:
        latest = self._latest(candidate_id)
        if latest.state is not KrLoopCandidateState.CANDIDATE_READY:
            if latest.verification_sha256 == receipt.receipt_id:
                return latest
            raise InvalidKrLoopEngineerControllerError
        reasons = validation_reasons(latest, receipt)
        if reasons:
            snapshot = build_candidate_snapshot(
                bundle_id=latest.bundle_id,
                base_commit=latest.base_commit,
                allowed_paths=latest.allowed_paths,
                state=KrLoopCandidateState.REJECTED,
                updated_at=receipt.verified_at,
                previous=latest,
                candidate_commit=latest.candidate_commit,
                patch_sha256=latest.patch_sha256,
                reason_codes=reasons,
            )
        else:
            snapshot = build_candidate_snapshot(
                bundle_id=latest.bundle_id,
                base_commit=latest.base_commit,
                allowed_paths=latest.allowed_paths,
                state=KrLoopCandidateState.SHADOWING,
                updated_at=receipt.verified_at,
                previous=latest,
                candidate_commit=latest.candidate_commit,
                patch_sha256=latest.patch_sha256,
                verification_sha256=receipt.receipt_id,
            )
        _ = self.store.append(snapshot)
        return snapshot

    def record_shadow(
        self,
        candidate_id: str,
        receipt: KrLoopShadowReceipt,
    ) -> KrLoopCandidateSnapshot:
        latest = self._latest(candidate_id)
        if latest.state is KrLoopCandidateState.PROMOTED:
            return latest
        if latest.state is not KrLoopCandidateState.SHADOWING:
            raise InvalidKrLoopEngineerControllerError
        same_session = tuple(item for item in latest.shadow_receipts if item.session_date == receipt.session_date)
        if same_session:
            if same_session == (receipt,):
                return latest
            raise InvalidKrLoopEngineerControllerError
        shadowing = build_candidate_snapshot(
            bundle_id=latest.bundle_id,
            base_commit=latest.base_commit,
            allowed_paths=latest.allowed_paths,
            state=KrLoopCandidateState.SHADOWING,
            updated_at=receipt.observed_at,
            previous=latest,
            candidate_commit=latest.candidate_commit,
            patch_sha256=latest.patch_sha256,
            verification_sha256=latest.verification_sha256,
            shadow_receipts=(*latest.shadow_receipts, receipt),
        )
        if promotion_blocked(shadowing):
            _ = self.store.append(shadowing)
            return shadowing
        promoted = build_candidate_snapshot(
            bundle_id=latest.bundle_id,
            base_commit=latest.base_commit,
            allowed_paths=latest.allowed_paths,
            state=KrLoopCandidateState.PROMOTED,
            updated_at=receipt.observed_at,
            previous=shadowing,
            candidate_commit=latest.candidate_commit,
            patch_sha256=latest.patch_sha256,
            verification_sha256=latest.verification_sha256,
            shadow_receipts=shadowing.shadow_receipts,
        )
        _ = self.store.append(shadowing)
        releases = self.store.releases()
        release = build_release_event(
            action=KrLoopReleaseAction.PROMOTE,
            candidate=promoted,
            previous=None if not releases else releases[-1],
            recorded_at=receipt.observed_at,
        )
        _ = self.store.append_release(promoted, release)
        return promoted

    def record_health(self, receipt: KrLoopHealthReceipt) -> KrLoopCandidateSnapshot:
        releases = self.store.releases()
        source = tuple(item for item in releases if item.release_id == receipt.release_id)
        if len(source) != 1:
            raise InvalidKrLoopEngineerControllerError
        candidate = self._latest(source[0].candidate_id)
        if candidate.state is KrLoopCandidateState.ROLLED_BACK:
            if receipt in candidate.health_receipts:
                return candidate
            raise InvalidKrLoopEngineerControllerError
        if candidate.state is not KrLoopCandidateState.PROMOTED or releases[-1] != source[0]:
            raise InvalidKrLoopEngineerControllerError
        reasons = health_reasons(receipt)
        if not reasons:
            return candidate
        rolled_back = build_candidate_snapshot(
            bundle_id=candidate.bundle_id,
            base_commit=candidate.base_commit,
            allowed_paths=candidate.allowed_paths,
            state=KrLoopCandidateState.ROLLED_BACK,
            updated_at=receipt.observed_at,
            previous=candidate,
            candidate_commit=candidate.candidate_commit,
            patch_sha256=candidate.patch_sha256,
            verification_sha256=candidate.verification_sha256,
            shadow_receipts=candidate.shadow_receipts,
            health_receipts=(receipt,),
            reason_codes=reasons,
        )
        rollback = build_release_event(
            action=KrLoopReleaseAction.ROLLBACK,
            candidate=rolled_back,
            previous=releases[-1],
            recorded_at=receipt.observed_at,
        )
        _ = self.store.append_release(rolled_back, rollback)
        return rolled_back

    def _latest(self, candidate_id: str) -> KrLoopCandidateSnapshot:
        value = self.store.latest(candidate_id)
        if value is None:
            raise InvalidKrLoopEngineerControllerError
        return value


__all__ = ("InvalidKrLoopEngineerControllerError", "KrLoopEngineerController")

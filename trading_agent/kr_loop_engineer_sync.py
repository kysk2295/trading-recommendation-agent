from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import override

from trading_agent.autonomous_memory_models import AutonomousMemoryScope
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.kr_autonomous_operator_paths import KrAutonomousOperatorPaths
from trading_agent.kr_autonomous_outcome_models import KrLoopEngineerEvidenceBundle, KrLoopFailureCode
from trading_agent.kr_loop_engineer_models import KrLoopCandidateState, build_candidate_snapshot
from trading_agent.kr_loop_engineer_policy import mutation_contract
from trading_agent.kr_loop_engineer_store import KrLoopEngineerStore


@dataclass(frozen=True, slots=True)
class KrLoopBundleSyncResult:
    inserted: int
    candidate_ids: tuple[str, ...]


class InvalidKrLoopBundleSyncError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR Loop Engineer bundle synchronization is invalid"


def pending_kr_loop_bundles(paths: KrAutonomousOperatorPaths) -> tuple[KrLoopEngineerEvidenceBundle, ...]:
    refs = tuple(sorted(f"failure:{failure.value}" for failure in KrLoopFailureCode))
    records = (
        AutonomousMemoryStore(paths.memory_database)
        .reader()
        .search(
            AutonomousMemoryScope.SELF_IMPROVEMENT,
            refs,
            limit=32,
        )
    )
    bundles: dict[str, KrLoopEngineerEvidenceBundle] = {}
    for record in records:
        bundle = KrLoopEngineerEvidenceBundle.model_validate_json(record.summary)
        if bundle.bundle_id not in record.inference_refs or not set(bundle.source_memory_ids) <= set(record.fact_refs):
            continue
        bundles[bundle.bundle_id] = bundle
    return tuple(sorted(bundles.values(), key=lambda item: (item.created_at, item.bundle_id)))


def sync_kr_loop_bundles(
    paths: KrAutonomousOperatorPaths,
    *,
    base_commit: str,
    now: dt.datetime,
) -> KrLoopBundleSyncResult:
    del now
    store = KrLoopEngineerStore(paths.loop_database)
    inserted = 0
    candidate_ids: list[str] = []
    for bundle in pending_kr_loop_bundles(paths):
        contract = mutation_contract(bundle, base_commit)
        detected = build_candidate_snapshot(
            bundle_id=bundle.bundle_id,
            base_commit=base_commit,
            allowed_paths=contract.allowed_paths,
            state=KrLoopCandidateState.DETECTED,
            updated_at=bundle.created_at,
        )
        candidate_ids.append(detected.candidate_id)
        if store.latest(detected.candidate_id) is None:
            inserted += store.append(detected)
    return KrLoopBundleSyncResult(inserted, tuple(candidate_ids))


def find_kr_loop_bundle(
    paths: KrAutonomousOperatorPaths,
    bundle_id: str,
) -> KrLoopEngineerEvidenceBundle | None:
    matches = tuple(bundle for bundle in pending_kr_loop_bundles(paths) if bundle.bundle_id == bundle_id)
    if len(matches) > 1:
        raise InvalidKrLoopBundleSyncError
    return matches[0] if matches else None


__all__ = (
    "InvalidKrLoopBundleSyncError",
    "KrLoopBundleSyncResult",
    "find_kr_loop_bundle",
    "pending_kr_loop_bundles",
    "sync_kr_loop_bundles",
)

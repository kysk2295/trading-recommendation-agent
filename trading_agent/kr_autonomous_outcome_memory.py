from __future__ import annotations

import hashlib
from typing import assert_never

from trading_agent.autonomous_memory_models import AutonomousMemoryRecord, AutonomousMemoryScope
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_task_models import AutonomousTaskId
from trading_agent.kr_autonomous_outcome_models import (
    KrAutonomousOutcomeMemory,
    KrLoopEngineerEvidenceBundle,
    KrLoopFailureCode,
    KrOutcomeExecutionState,
    canonical_kr_autonomous_outcome_json,
    canonical_kr_loop_engineer_bundle_json,
    kr_loop_engineer_bundle_id,
)
from trading_agent.kr_autonomous_outcome_observation import outcome_memory_key
from trading_agent.kr_autonomous_trade_models import KrCriticReason, KrNoTradeReason


def outcome_record(
    memory: AutonomousMemoryStore,
    outcome: KrAutonomousOutcomeMemory,
) -> AutonomousMemoryRecord | None:
    key = outcome_memory_key(outcome)
    summary = canonical_kr_autonomous_outcome_json(outcome)
    latest = memory.reader().latest(key)
    if latest is not None and latest.summary == summary:
        return None
    refs = (outcome.trade_event_id,)
    if outcome.position_event_id is not None:
        refs = tuple(sorted((*refs, outcome.position_event_id)))
    subjects = tuple(
        sorted(
            {
                f"symbol:{outcome.symbol}",
                f"theme:{hashlib.sha256(outcome.theme.encode()).hexdigest()[:16]}",
                f"verification:{outcome.verification_state.value}",
                f"market:{outcome.market_evidence_state.value}",
                f"session:{outcome.session_phase.value}",
                *(f"source-cluster:{item}" for item in outcome.independent_source_cluster_ids),
            }
        )
    )
    return AutonomousMemoryRecord.model_validate(
        {
            "memory_key": key,
            "version": 1 if latest is None else latest.version + 1,
            "scope": AutonomousMemoryScope.MARKET,
            "summary": summary,
            "fact_refs": refs,
            "inference_refs": (),
            "subject_refs": subjects[:32],
            "evidence_refs": outcome.evidence_refs,
            "source_task_ids": (AutonomousTaskId(outcome.task_id),),
            "recorded_at": outcome.observed_at,
        }
    )


def append_records(memory: AutonomousMemoryStore, records: tuple[AutonomousMemoryRecord, ...]) -> int:
    if not records:
        return 0
    with memory.writer() as writer:
        return sum(writer.append(record) for record in records)


def bundle_records(
    memory: AutonomousMemoryStore,
    outcomes: tuple[KrAutonomousOutcomeMemory, ...],
) -> tuple[AutonomousMemoryRecord | None, ...]:
    groups: dict[tuple[KrLoopFailureCode, str], list[AutonomousMemoryRecord]] = {}
    for outcome in outcomes:
        failure = _failure(outcome)
        latest = memory.reader().latest(outcome_memory_key(outcome))
        if failure is not None and latest is not None:
            groups.setdefault((failure, f"symbol:{outcome.symbol}"), []).append(latest)
    return tuple(
        _bundle_record(memory, failure, subject, tuple(records))
        for (failure, subject), records in sorted(groups.items(), key=lambda item: (item[0][0].value, item[0][1]))
        if len({record.memory_id for record in records}) >= 3
    )


def _bundle_record(
    memory: AutonomousMemoryStore,
    failure: KrLoopFailureCode,
    subject: str,
    records: tuple[AutonomousMemoryRecord, ...],
) -> AutonomousMemoryRecord | None:
    selected = tuple(sorted(records, key=lambda item: (item.recorded_at, item.memory_id))[-16:])
    evidence = tuple(sorted({ref for record in selected for ref in record.evidence_refs}))[:16]
    task_ids = tuple(sorted({task_id for record in selected for task_id in record.source_task_ids}))[:16]
    draft = KrLoopEngineerEvidenceBundle.model_construct(
        bundle_id="",
        failure_code=failure,
        subject_ref=subject,
        source_memory_ids=tuple(sorted(record.memory_id for record in selected)),
        source_task_ids=task_ids,
        evidence_refs=evidence,
        change_hypothesis=_change_hypothesis(failure),
        created_at=max(record.recorded_at for record in selected),
    )
    bundle = KrLoopEngineerEvidenceBundle.model_validate(
        draft.model_copy(update={"bundle_id": kr_loop_engineer_bundle_id(draft)}).model_dump(mode="python")
    )
    key = f"self_improvement.kr.{failure.value}.{hashlib.sha256(subject.encode()).hexdigest()[:16]}"
    summary = canonical_kr_loop_engineer_bundle_json(bundle)
    latest = memory.reader().latest(key)
    if latest is not None and latest.summary == summary:
        return None
    return AutonomousMemoryRecord.model_validate(
        {
            "memory_key": key,
            "version": 1 if latest is None else latest.version + 1,
            "scope": AutonomousMemoryScope.SELF_IMPROVEMENT,
            "summary": summary,
            "fact_refs": bundle.source_memory_ids,
            "inference_refs": (bundle.bundle_id,),
            "subject_refs": tuple(sorted((subject, f"failure:{failure.value}"))),
            "evidence_refs": bundle.evidence_refs,
            "source_task_ids": tuple(AutonomousTaskId(item) for item in bundle.source_task_ids),
            "recorded_at": bundle.created_at,
        }
    )


def _failure(outcome: KrAutonomousOutcomeMemory) -> KrLoopFailureCode | None:
    if outcome.execution_state is KrOutcomeExecutionState.VIRTUAL_STOPPED:
        return KrLoopFailureCode.VIRTUAL_STOP
    if outcome.execution_state is KrOutcomeExecutionState.VIRTUAL_CENSORED:
        return KrLoopFailureCode.VIRTUAL_CENSORED
    reasons = set(outcome.decision_reason_codes)
    if KrCriticReason.CLUSTER_COUNT.value in reasons:
        return KrLoopFailureCode.CRITIC_CLUSTER_COUNT
    if KrCriticReason.NONCAUSAL_PUBLICATION.value in reasons:
        return KrLoopFailureCode.CRITIC_CHRONOLOGY
    if reasons & {KrNoTradeReason.STALE_MARKET.value, KrNoTradeReason.MISSING_SPREAD.value}:
        return KrLoopFailureCode.MARKET_DATA
    return None


def _change_hypothesis(failure: KrLoopFailureCode) -> str:
    match failure:
        case KrLoopFailureCode.CRITIC_CHRONOLOGY:
            return "Tighten publication-to-market chronology checks and replay the affected evidence lineage."
        case KrLoopFailureCode.CRITIC_CLUSTER_COUNT:
            return "Re-evaluate repost clustering and independent-source admission against the affected evidence."
        case KrLoopFailureCode.MARKET_DATA:
            return "Review stored market freshness and spread admission without widening provider authority."
        case KrLoopFailureCode.VIRTUAL_CENSORED:
            return "Inspect completed-bar continuity and restart reconciliation for the censored virtual paths."
        case KrLoopFailureCode.VIRTUAL_STOP:
            return "Review the entry and invalidation hypothesis across the repeated virtual stop outcomes."
        case unreachable:
            assert_never(unreachable)


__all__ = ("append_records", "bundle_records", "outcome_record")

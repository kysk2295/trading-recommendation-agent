from __future__ import annotations

import datetime as dt
import hashlib
from typing import Final, assert_never

from trading_agent.autonomous_memory_models import AutonomousMemoryRecord, AutonomousMemoryScope
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.dashboard_outbound_redaction import redact_outbound_text, require_safe_outbound_text
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import HermesProjectionRecord, HermesProjectionResult, project_outcomes
from trading_agent.hermes_delivery_store import HermesDeliveryWriter
from trading_agent.kr_autonomous_operator_paths import KrAutonomousOperatorPaths
from trading_agent.kr_autonomous_outcome_models import (
    InvalidKrAutonomousOutcomeError,
    KrAutonomousOutcomeMemory,
    KrLoopEngineerEvidenceBundle,
    KrLoopFailureCode,
    execution_state_label,
)
from trading_agent.kr_autonomous_trade_models import (
    KrAutonomousNoTrade,
    KrAutonomousRejected,
    KrAutonomousTradeEvent,
    KrTradeRecommendation,
)
from trading_agent.kr_autonomous_trade_store import KrAutonomousTradeStore
from trading_agent.kr_loop_engineer_hermes import kr_loop_hermes_fields
from trading_agent.kr_loop_engineer_models import KrLoopCandidateSnapshot
from trading_agent.kr_loop_engineer_store import KrLoopEngineerStore
from trading_agent.kr_virtual_position_models import KrVirtualPositionEvent
from trading_agent.kr_virtual_position_store import KrVirtualPositionStore

_MARKET_ID: Final = "kr_equities"
_AGENT_FAMILY: Final = "day_trading"
_LANE_ID: Final = "kr_autonomous"
_STRATEGY_VERSION: Final = "kr-autonomous-v1"


def project_kr_autonomous_state(
    paths: KrAutonomousOperatorPaths,
    writer: HermesDeliveryWriter,
    *,
    projected_source_ids: frozenset[str],
) -> HermesProjectionResult:
    trades = KrAutonomousTradeStore(paths.trade_database).events()
    positions = KrVirtualPositionStore(paths.position_database).all_events()
    memories = _memory_records(paths, trades)
    loop_snapshots = KrLoopEngineerStore(paths.loop_database).snapshots()
    records = (
        *(_trade_record(event) for event in trades if event.event_id not in projected_source_ids),
        *(_position_record(event) for event in positions if event.event_id not in projected_source_ids),
        *(_memory_record(record) for record in memories if record.memory_id not in projected_source_ids),
        *(_loop_record(snapshot) for snapshot in loop_snapshots if snapshot.snapshot_id not in projected_source_ids),
    )
    return project_outcomes(records, writer)


def _memory_records(
    paths: KrAutonomousOperatorPaths,
    trades: tuple[KrAutonomousTradeEvent, ...],
) -> tuple[AutonomousMemoryRecord, ...]:
    reader = AutonomousMemoryStore(paths.memory_database).reader()
    records = {
        record.memory_id: record
        for event in trades
        for record in reader.history(f"market.kr.{event.symbol}.{event.event_id[:24]}")
    }
    symbols = tuple(sorted({event.symbol for event in trades}))
    for symbol in symbols:
        subject = f"symbol:{symbol}"
        subject_hash = hashlib.sha256(subject.encode()).hexdigest()[:16]
        for failure in KrLoopFailureCode:
            key = f"self_improvement.kr.{failure.value}.{subject_hash}"
            records.update((record.memory_id, record) for record in reader.history(key))
    failure_refs = tuple(sorted(f"failure:{failure.value}" for failure in KrLoopFailureCode))
    records.update(
        (record.memory_id, record)
        for record in reader.search(AutonomousMemoryScope.SELF_IMPROVEMENT, failure_refs, limit=32)
    )
    return tuple(sorted(records.values(), key=lambda item: (item.recorded_at, item.memory_id)))


def _trade_record(event: KrAutonomousTradeEvent) -> HermesProjectionRecord:
    match event:
        case KrTradeRecommendation():
            kind = HermesDeliveryKind.ACTIONABLE
            status = "virtual_recommendation"
            evidence = event.evidence_refs
            text = (
                f"[한국시장 가상 추천] {event.symbol} 진입={event.entry}, 손절={event.stop}, "
                f"목표={event.targets[0]}/{event.targets[1]}, 수량={event.quantity}. "
                f"근거={event.rationale} 다음 확인={event.valid_until.isoformat()}. 실거래 권한=false."
            )
        case KrAutonomousNoTrade():
            kind = HermesDeliveryKind.NO_RECOMMENDATION
            status = "no_trade"
            evidence = ()
            reasons = ",".join(reason.value for reason in event.reason_codes)
            text = (
                f"[한국시장 관망] {event.symbol} 사유={reasons}. "
                f"다음 확인={event.next_wake_at.isoformat()}. 실거래 권한=false."
            )
        case KrAutonomousRejected():
            kind = HermesDeliveryKind.NO_RECOMMENDATION
            status = "rejected"
            evidence = ()
            reasons = ",".join(reason.value for reason in event.reason_codes)
            text = (
                f"[한국시장 기각] {event.symbol} 사유={reasons}. "
                f"다음 확인={event.next_wake_at.isoformat()}. 실거래 권한=false."
            )
        case unreachable:
            assert_never(unreachable)
    return _record(
        source_event_id=event.event_id,
        root_source_event_id=None,
        kind=kind,
        instrument_id=event.symbol,
        occurred_at=event.timestamp,
        status=status,
        evidence_refs=evidence,
        rendered_text=text,
        payload=event.model_dump_json(),
    )


def _position_record(event: KrVirtualPositionEvent) -> HermesProjectionRecord:
    fill = "-" if event.fill_price is None else str(event.fill_price)
    exit_price = "-" if event.exit_price is None else str(event.exit_price)
    text = (
        f"[한국시장 가상 포지션] {event.symbol} 상태={event.state.value}, 사유={event.reason.value}, "
        f"진입={event.entry}, 손절={event.stop}, 목표={event.targets[0]}/{event.targets[1]}, "
        f"가상체결={fill}, 가상청산={exit_price}. 실거래 권한=false."
    )
    return _record(
        source_event_id=event.event_id,
        root_source_event_id=event.recommendation_id,
        kind=HermesDeliveryKind.EXIT if event.terminal else HermesDeliveryKind.ACTIONABLE,
        instrument_id=event.symbol,
        occurred_at=event.occurred_at,
        status=f"virtual_{event.state.value.lower()}",
        evidence_refs=event.evidence_refs,
        rendered_text=text,
        payload=event.model_dump_json(),
    )


def _memory_record(record: AutonomousMemoryRecord) -> HermesProjectionRecord:
    match record.scope:
        case AutonomousMemoryScope.MARKET:
            outcome = KrAutonomousOutcomeMemory.model_validate_json(record.summary)
            reactions = ", ".join(f"{item.horizon.value}:{item.return_bps:+f}bps" for item in outcome.horizons)
            text = (
                f"[한국시장 가상 결과 학습] {outcome.symbol} 판정={execution_state_label(outcome.execution_state)}, "
                f"시장반응={reactions or '-'}. 원본은 append-only이며 실거래 권한=false."
            )
            return _record(
                source_event_id=str(record.memory_id),
                root_source_event_id=outcome.trade_event_id,
                kind=HermesDeliveryKind.RESEARCH,
                instrument_id=outcome.symbol,
                occurred_at=record.recorded_at,
                status=outcome.execution_state.value,
                evidence_refs=record.evidence_refs,
                rendered_text=text,
                payload=record.model_dump_json(),
            )
        case AutonomousMemoryScope.SELF_IMPROVEMENT:
            bundle = KrLoopEngineerEvidenceBundle.model_validate_json(record.summary)
            text = (
                f"[Loop Engineer 증거 묶음] 반복 실패={bundle.failure_code.value}, 대상={bundle.subject_ref}, "
                f"표본={len(bundle.source_memory_ids)}. 변경 가설={bundle.change_hypothesis} "
                "코드 변경 권한=false."
            )
            instrument = (
                bundle.subject_ref.removeprefix("symbol:") if bundle.subject_ref.startswith("symbol:") else None
            )
            return _record(
                source_event_id=str(record.memory_id),
                root_source_event_id=None,
                kind=HermesDeliveryKind.INCIDENT,
                instrument_id=instrument,
                occurred_at=record.recorded_at,
                status="loop_evidence_bundle",
                evidence_refs=record.evidence_refs,
                rendered_text=text,
                payload=record.model_dump_json(),
            )
        case AutonomousMemoryScope.WORK | AutonomousMemoryScope.STRATEGY:
            raise InvalidKrAutonomousOutcomeError
        case unreachable:
            assert_never(unreachable)


def _loop_record(snapshot: KrLoopCandidateSnapshot) -> HermesProjectionRecord:
    fields = kr_loop_hermes_fields(snapshot)
    return _record(
        source_event_id=snapshot.snapshot_id,
        root_source_event_id=None,
        kind=fields.kind,
        instrument_id=None,
        occurred_at=snapshot.updated_at,
        status=fields.status,
        evidence_refs=fields.evidence_refs,
        rendered_text=fields.rendered_text,
        payload=snapshot.model_dump_json(),
    )


def _record(
    *,
    source_event_id: str,
    root_source_event_id: str | None,
    kind: HermesDeliveryKind,
    instrument_id: str | None,
    occurred_at: dt.datetime,
    status: str,
    evidence_refs: tuple[str, ...],
    rendered_text: str,
    payload: str,
) -> HermesProjectionRecord:
    text = redact_outbound_text(rendered_text, max_chars=4096).strip()
    require_safe_outbound_text(text)
    return HermesProjectionRecord(
        source_event_id=source_event_id,
        root_source_event_id=root_source_event_id,
        kind=kind,
        market_id=_MARKET_ID,
        agent_family=_AGENT_FAMILY,
        lane_id=_LANE_ID,
        strategy_version=_STRATEGY_VERSION,
        instrument_id=instrument_id,
        occurred_at=occurred_at,
        status=status,
        evidence_refs=tuple(sorted(set(evidence_refs))),
        rendered_text=text,
        payload_sha256=hashlib.sha256(payload.encode()).hexdigest(),
    )


__all__ = ("project_kr_autonomous_state",)

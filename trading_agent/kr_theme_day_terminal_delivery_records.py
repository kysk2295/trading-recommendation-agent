from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from typing import assert_never

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import HermesProjectionRecord
from trading_agent.kr_theme_day_shadow_entry_models import KrThemeDayShadowEntry
from trading_agent.kr_theme_day_shadow_exit_models import KrThemeDayShadowExit
from trading_agent.kr_theme_day_trial_terminal_models import (
    KrThemeDayTrialTerminalArtifact,
    KrThemeDayTrialTerminalReason,
)
from trading_agent.kr_theme_lane import KR_THEME_LEADER_VWAP_RECLAIM_LANE


class InvalidKrThemeDayTerminalDeliveryRecordError(ValueError):
    pass


def build_kr_theme_day_terminal_delivery_records(
    artifact: KrThemeDayTrialTerminalArtifact,
    entries: tuple[KrThemeDayShadowEntry, ...],
    exits: tuple[KrThemeDayShadowExit, ...],
) -> tuple[HermesProjectionRecord, ...]:
    match artifact.payload.terminal_kind:
        case TrialEventKind.COMPLETED:
            by_entry = {item.entry_id: item for item in entries}
            if len(by_entry) != len(exits) or any(item.entry_id not in by_entry for item in exits):
                raise InvalidKrThemeDayTerminalDeliveryRecordError
            return tuple(_exit_record(artifact, by_entry[item.entry_id], item) for item in exits)
        case TrialEventKind.CENSORED:
            no_entry = (KrThemeDayTrialTerminalReason.NO_SHADOW_ENTRY_ARTIFACT.value,)
            if artifact.payload.reason_codes == no_entry and not entries and not exits:
                return (_no_recommendation_record(artifact),)
            return (_incident_record(artifact, entries),)
        case TrialEventKind.FAILED:
            return (_incident_record(artifact, entries),)
        case TrialEventKind.STARTED:
            raise InvalidKrThemeDayTerminalDeliveryRecordError
        case unreachable:
            assert_never(unreachable)


def _exit_record(
    artifact: KrThemeDayTrialTerminalArtifact,
    entry: KrThemeDayShadowEntry,
    exit: KrThemeDayShadowExit,
) -> HermesProjectionRecord:
    return _record(
        source_event_id=f"kr-exit:{exit.exit_id}",
        root_source_event_id=entry.signal_id,
        kind=HermesDeliveryKind.EXIT,
        strategy_version=exit.strategy_version,
        instrument_id=exit.symbol,
        occurred_at=exit.exit_at,
        status=exit.reason.value,
        evidence_refs=(
            f"entry:{entry.entry_id}",
            f"exit:{exit.exit_id}",
            f"terminal:{artifact.artifact_id}",
        ),
        rendered_text=(
            "KR Theme Day shadow 종료\n"
            f"- 종목: {exit.symbol}\n"
            f"- 종료 사유: {exit.reason.value}\n"
            f"- shadow 진입/청산가: {_decimal(exit.entry_fill_price)} / {_decimal(exit.exit_price)}\n"
            f"- 순수익률: {_decimal(exit.net_return * Decimal(100))}%\n"
            f"- 실현 R: {_decimal(exit.realized_r)}\n"
            "- 국내 계좌 주문: 없음"
        ),
        payload_sha256=_payload_sha256(exit),
    )


def _no_recommendation_record(artifact: KrThemeDayTrialTerminalArtifact) -> HermesProjectionRecord:
    return _record(
        source_event_id=f"kr-terminal:{artifact.artifact_id}",
        root_source_event_id=None,
        kind=HermesDeliveryKind.NO_RECOMMENDATION,
        strategy_version=artifact.payload.strategy_version,
        instrument_id=None,
        occurred_at=artifact.payload.terminal_at,
        status=KrThemeDayTrialTerminalReason.NO_SHADOW_ENTRY_ARTIFACT.value,
        evidence_refs=(f"terminal:{artifact.artifact_id}",),
        rendered_text=(
            "KR Theme Day 장 마감: 추천 없음\n"
            "- 현재시점 인과성 게이트를 통과한 shadow 진입이 없었습니다.\n"
            "- 수익률 0으로 평가하지 않고 censored session으로 보존합니다.\n"
            "- 국내 계좌 주문: 없음"
        ),
        payload_sha256=_payload_sha256(artifact),
    )


def _incident_record(
    artifact: KrThemeDayTrialTerminalArtifact,
    entries: tuple[KrThemeDayShadowEntry, ...],
) -> HermesProjectionRecord:
    root = entries[0].signal_id if len(entries) == 1 else None
    symbol = entries[0].symbol if len(entries) == 1 else None
    reasons = ", ".join(artifact.payload.reason_codes)
    return _record(
        source_event_id=f"kr-terminal:{artifact.artifact_id}",
        root_source_event_id=root,
        kind=HermesDeliveryKind.INCIDENT,
        strategy_version=artifact.payload.strategy_version,
        instrument_id=symbol,
        occurred_at=artifact.payload.terminal_at,
        status=artifact.payload.reason_codes[0],
        evidence_refs=(
            *(f"entry:{item.entry_id}" for item in entries),
            f"terminal:{artifact.artifact_id}",
        ),
        rendered_text=(
            "KR Theme Day shadow 경로 불완전\n"
            f"- 차단 사유: {reasons}\n"
            "- 성과 표본에서 제외하고 운영 점검이 필요합니다.\n"
            "- 국내 계좌 주문: 없음"
        ),
        payload_sha256=_payload_sha256(artifact),
    )


def _record(
    *,
    source_event_id: str,
    root_source_event_id: str | None,
    kind: HermesDeliveryKind,
    strategy_version: str,
    instrument_id: str | None,
    occurred_at: dt.datetime,
    status: str,
    evidence_refs: tuple[str, ...],
    rendered_text: str,
    payload_sha256: str,
) -> HermesProjectionRecord:
    lane = KR_THEME_LEADER_VWAP_RECLAIM_LANE
    return HermesProjectionRecord(
        source_event_id=source_event_id,
        root_source_event_id=root_source_event_id,
        kind=kind,
        market_id=lane.market_id.value,
        agent_family=lane.agent_family.value,
        lane_id=lane.canonical_id,
        strategy_version=strategy_version,
        instrument_id=instrument_id,
        occurred_at=occurred_at,
        status=status,
        evidence_refs=tuple(sorted(evidence_refs)),
        rendered_text=rendered_text,
        payload_sha256=payload_sha256,
    )


def _payload_sha256(value: KrThemeDayShadowExit | KrThemeDayTrialTerminalArtifact) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(value).encode()).hexdigest()


def _decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


__all__ = ("build_kr_theme_day_terminal_delivery_records",)

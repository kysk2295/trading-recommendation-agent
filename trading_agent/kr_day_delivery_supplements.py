from __future__ import annotations

import datetime as dt
import hashlib
from typing import Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.day_learning_report_models import MarketCloseReport
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import HermesProjectionRecord
from trading_agent.kr_theme_lane import KR_THEME_LEADER_VWAP_RECLAIM_LANE

_HEX64 = r"^[0-9a-f]{64}$"


class InvalidKrDayDeliverySupplementError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day delivery supplement is invalid"


class KrDayDeliveryIncident(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    incident_id: str = Field(pattern=_HEX64)
    occurred_at: dt.datetime
    scope: str
    reason_codes: tuple[str, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    capsule_id: str | None = Field(default=None, pattern=_HEX64)
    symbol: str | None = None

    @model_validator(mode="after")
    def validate_incident(self) -> Self:
        if (
            self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
            or not self.scope
            or self.scope != self.scope.strip()
            or self.reason_codes != tuple(sorted(set(self.reason_codes)))
            or self.evidence_refs != tuple(sorted(set(self.evidence_refs)))
            or any(not value or value != value.strip() for value in (*self.reason_codes, *self.evidence_refs))
        ):
            raise InvalidKrDayDeliverySupplementError
        return self


def build_kr_day_supplement_records(
    incidents: tuple[KrDayDeliveryIncident, ...],
    reports: tuple[MarketCloseReport, ...],
) -> tuple[HermesProjectionRecord, ...]:
    return tuple(_incident_record(item) for item in incidents) + tuple(_summary_record(item) for item in reports)


def _incident_record(incident: KrDayDeliveryIncident) -> HermesProjectionRecord:
    return _record(
        source_id=f"kr-day:incident:{incident.incident_id}",
        kind=HermesDeliveryKind.INCIDENT,
        occurred_at=incident.occurred_at,
        status=incident.reason_codes[0],
        strategy_version=incident.capsule_id,
        symbol=incident.symbol,
        evidence=incident.evidence_refs,
        text=(
            "KR Day 서비스/데이터 장애\n"
            f"- 영향 범위: {incident.scope}\n"
            f"- 실패 사유: {', '.join(incident.reason_codes)}"
        ),
        payload_sha256=hashlib.sha256(incident.model_dump_json().encode()).hexdigest(),
    )


def _summary_record(report: MarketCloseReport) -> HermesProjectionRecord:
    payload = report.payload
    execution = payload.execution
    research = payload.research
    next_session = payload.next_session
    diagnostics = tuple(reason for item in payload.diagnostics for reason in item.reason_codes)
    return _record(
        source_id=f"kr-day:summary:{report.report_id}",
        kind=HermesDeliveryKind.DAILY_SUMMARY,
        occurred_at=payload.finalized_at,
        status="daily_summary",
        strategy_version=payload.agent_version_id,
        symbol=None,
        evidence=tuple(sorted((*payload.watermark.source_event_ids, f"report:{report.report_id}"))),
        text=(
            f"KR Day 장 마감 요약 ({payload.session_date.isoformat()})\n"
            f"- shadow 수익률: {execution.modeled_return:.6f}\n"
            f"- 시도/지지/반박/불확정: {research.attempted_variant_count}/"
            f"{research.supported_count}/{research.refuted_count}/{research.inconclusive_count}\n"
            f"- censored/unresolved: {execution.censored_count}/{execution.unresolved_count}\n"
            f"- 실패: {', '.join(diagnostics) if diagnostics else '없음'}\n"
            f"- challenger 결정 active/queued: {len(next_session.active_capsule_ids)}/"
            f"{len(next_session.queued_capsule_ids)}\n"
            "- 국내 provider read-only, 실계좌 주문 없음"
        ),
        payload_sha256=hashlib.sha256(canonical_experiment_ledger_json(report).encode()).hexdigest(),
    )


def _record(
    *,
    source_id: str,
    kind: HermesDeliveryKind,
    occurred_at: dt.datetime,
    status: str,
    strategy_version: str | None,
    symbol: str | None,
    evidence: tuple[str, ...],
    text: str,
    payload_sha256: str,
) -> HermesProjectionRecord:
    lane = KR_THEME_LEADER_VWAP_RECLAIM_LANE
    return HermesProjectionRecord(
        source_event_id=source_id,
        root_source_event_id=None,
        kind=kind,
        market_id=lane.market_id.value,
        agent_family=lane.agent_family.value,
        lane_id=lane.canonical_id,
        strategy_version=strategy_version,
        instrument_id=symbol,
        occurred_at=occurred_at,
        status=status,
        evidence_refs=evidence,
        rendered_text=text,
        payload_sha256=payload_sha256,
    )


__all__ = ("KrDayDeliveryIncident", "build_kr_day_supplement_records")

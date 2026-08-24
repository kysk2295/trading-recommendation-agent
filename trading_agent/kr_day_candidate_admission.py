from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal, InvalidOperation
from typing import Final
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.kr_day_decision_models import (
    InvalidKrDayCandidateAdmissionError,
    KrDayCandidateAdmissionPolicy,
    KrDayCandidateAdmissionRequest,
    KrDayCandidateAdmissionResult,
    KrDayDecisionEvidenceValue,
    KrDayDecisionReasonCode,
    KrDayDecisionStatus,
)
from trading_agent.kr_intraday_market_gate import (
    KrIntradayGateReason,
    KrIntradayGateStatus,
    assess_kr_shadow_entry,
)
from trading_agent.kr_theme_day_setup import KrCompletedMinuteBar
from trading_agent.signal_contract_models import OpportunitySnapshot

_SEOUL: Final = ZoneInfo("Asia/Seoul")


def assess_kr_day_candidate_admission(request: KrDayCandidateAdmissionRequest) -> KrDayCandidateAdmissionResult:
    current = _validated_request(request)
    candidate = current.opportunity.candidates[0]
    values = {item.name: item.value for item in candidate.features}
    reasons: set[KrDayDecisionReasonCode] = set()
    evidence = _feature_evidence(values)
    theme_name = values.get("theme_name", "")
    thesis_key = _thesis_key(current.evaluated_at, candidate.symbol, theme_name)
    _currentness_reasons(current, reasons)
    safe_bars = _safe_bars(current, candidate.symbol, reasons)
    _feature_reasons(values, current.policy, reasons)
    _bar_reasons(safe_bars, current.policy, evidence, reasons)
    _market_reasons(current, evidence, reasons)
    if thesis_key in current.active_thesis_keys:
        reasons.add(KrDayDecisionReasonCode.DUPLICATE_THESIS)
    evidence.extend(
        (
            KrDayDecisionEvidenceValue(name="thesis_key", value=thesis_key),
            KrDayDecisionEvidenceValue(
                name="opportunity_current",
                value=str(
                    not bool(
                        {KrDayDecisionReasonCode.OPPORTUNITY_EXPIRED, KrDayDecisionReasonCode.STALE_EVIDENCE} & reasons
                    )
                ).lower(),
            ),
        )
    )
    ordered_reasons = tuple(sorted(reasons, key=lambda item: item.value))
    status = _status(current, ordered_reasons)
    return KrDayCandidateAdmissionResult(
        admitted=status is KrDayDecisionStatus.INVESTIGATING,
        status=status,
        reason_codes=ordered_reasons,
        thesis_key=thesis_key,
        observed_evidence=tuple(sorted(evidence, key=lambda item: item.name)),
        source_evidence_refs=_source_refs(current, safe_bars),
    )


def kr_day_candidate_thesis_key(opportunity: OpportunitySnapshot, symbol: str) -> str:
    try:
        current = OpportunitySnapshot.model_validate(opportunity.model_dump(mode="python"))
        theme_name = next(item.value for item in current.candidates[0].features if item.name == "theme_name")
    except (AttributeError, StopIteration, TypeError, ValidationError, ValueError):
        raise InvalidKrDayCandidateAdmissionError from None
    return _thesis_key(current.observed_at, symbol, theme_name)


def _validated_request(request: KrDayCandidateAdmissionRequest) -> KrDayCandidateAdmissionRequest:
    try:
        current = KrDayCandidateAdmissionRequest.model_validate(request.model_dump(mode="python"))
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidKrDayCandidateAdmissionError from None
    if (current.policy.capsule_id, current.policy.hypothesis_version_id) != (
        current.capsule_id,
        current.hypothesis_version_id,
    ):
        raise InvalidKrDayCandidateAdmissionError
    return current


def _feature_evidence(values: dict[str, str]) -> list[KrDayDecisionEvidenceValue]:
    names = (
        "is_leader",
        "theme_name",
        "theme_catalyst_count",
        "theme_publisher_count",
        "theme_related_symbol_count",
        "trading_value_krw",
        "volume_ratio",
    )
    return [
        KrDayDecisionEvidenceValue(name=name, value=_observed_feature_value(name, values.get(name))) for name in names
    ]


def _currentness_reasons(request: KrDayCandidateAdmissionRequest, reasons: set[KrDayDecisionReasonCode]) -> None:
    if request.evaluated_at >= request.opportunity.valid_until:
        reasons.add(KrDayDecisionReasonCode.OPPORTUNITY_EXPIRED)
    if request.opportunity.observed_at > request.evaluated_at:
        reasons.add(KrDayDecisionReasonCode.STALE_EVIDENCE)


def _safe_bars(
    request: KrDayCandidateAdmissionRequest, symbol: str, reasons: set[KrDayDecisionReasonCode]
) -> tuple[KrCompletedMinuteBar, ...]:
    safe = tuple(bar for bar in request.bars if max(bar.end_at, bar.observed_at) <= request.evaluated_at)
    if len(safe) != len(request.bars) or not safe or any(bar.symbol != symbol for bar in safe):
        reasons.add(KrDayDecisionReasonCode.STALE_EVIDENCE)
    return tuple(sorted(safe, key=lambda bar: bar.end_at))


def _feature_reasons(
    values: dict[str, str], policy: KrDayCandidateAdmissionPolicy, reasons: set[KrDayDecisionReasonCode]
) -> None:
    leader = values.get("is_leader") == "true"
    theme = bool(values.get("theme_name", "").strip())
    breadth = _integer(values.get("theme_related_symbol_count"))
    if not leader or not theme or breadth is None or breadth < policy.min_related_symbol_count:
        reasons.add(KrDayDecisionReasonCode.THEME_BREADTH_MISSING)
    catalysts = _integer(values.get("theme_catalyst_count"))
    publishers = _integer(values.get("theme_publisher_count"))
    if (
        catalysts is None
        or publishers is None
        or catalysts < policy.min_catalyst_count
        or publishers < policy.min_publisher_count
    ):
        reasons.add(KrDayDecisionReasonCode.CATALYST_SOURCE_MISSING)
    ratio = _decimal(values.get("volume_ratio"))
    if ratio is None or ratio < policy.min_opportunity_volume_ratio:
        reasons.add(KrDayDecisionReasonCode.VOLUME_CONFIRMATION_MISSING)
    value = _decimal(values.get("trading_value_krw"))
    if value is None or value < policy.min_trading_value_krw:
        reasons.add(KrDayDecisionReasonCode.FLOW_CONFIRMATION_MISSING)


def _bar_reasons(
    bars: tuple[KrCompletedMinuteBar, ...],
    policy: KrDayCandidateAdmissionPolicy,
    evidence: list[KrDayDecisionEvidenceValue],
    reasons: set[KrDayDecisionReasonCode],
) -> None:
    if len(bars) < 2:
        evidence.extend(_bar_evidence("missing", "missing", "missing"))
        reasons.update(
            {KrDayDecisionReasonCode.VOLUME_CONFIRMATION_MISSING, KrDayDecisionReasonCode.FLOW_CONFIRMATION_MISSING}
        )
        return
    latest, previous = bars[-1], bars[-2]
    average_volume = sum(Decimal(bar.volume) for bar in bars[:-1]) / Decimal(len(bars) - 1)
    volume_ratio = Decimal(latest.volume) / average_volume
    price_response = latest.close / previous.close - Decimal(1)
    evidence.extend(_bar_evidence(str(volume_ratio), str(price_response), str(latest.trading_value_krw)))
    if volume_ratio < policy.min_completed_bar_volume_ratio:
        reasons.add(KrDayDecisionReasonCode.VOLUME_CONFIRMATION_MISSING)
    if (
        latest.trading_value_krw < policy.min_completed_bar_trading_value_krw
        or price_response < policy.min_completed_bar_price_response
    ):
        reasons.add(KrDayDecisionReasonCode.FLOW_CONFIRMATION_MISSING)


def _market_reasons(
    request: KrDayCandidateAdmissionRequest,
    evidence: list[KrDayDecisionEvidenceValue],
    reasons: set[KrDayDecisionReasonCode],
) -> None:
    gate = assess_kr_shadow_entry(request.market, request.evaluated_at)
    bid, ask = request.market.bid_price, request.market.ask_price
    evidence.append(
        KrDayDecisionEvidenceValue(
            name="market_gate_reasons", value=",".join(sorted(reason.value for reason in gate.reasons)) or "eligible"
        )
    )
    evidence.append(KrDayDecisionEvidenceValue(name="market_gate_status", value=gate.status.value))
    spread_bps = None if bid is None or ask is None else (ask - bid) / ((ask + bid) / Decimal(2)) * Decimal(10_000)
    spread_value = "missing" if spread_bps is None else str(spread_bps)
    evidence.append(KrDayDecisionEvidenceValue(name="spread_bps", value=spread_value))
    if gate.status is KrIntradayGateStatus.BLOCKED:
        reasons.add(KrDayDecisionReasonCode.MARKET_GATE_BLOCKED)
        if any(
            reason in {KrIntradayGateReason.STALE_EVIDENCE, KrIntradayGateReason.FUTURE_EVIDENCE}
            for reason in gate.reasons
        ):
            reasons.add(KrDayDecisionReasonCode.STALE_EVIDENCE)
        return
    if spread_bps is None:
        raise InvalidKrDayCandidateAdmissionError
    if spread_bps > request.policy.max_spread_bps:
        reasons.add(KrDayDecisionReasonCode.SPREAD_TOO_WIDE)


def _status(
    request: KrDayCandidateAdmissionRequest, reasons: tuple[KrDayDecisionReasonCode, ...]
) -> KrDayDecisionStatus:
    if KrDayDecisionReasonCode.OPPORTUNITY_EXPIRED in reasons:
        return KrDayDecisionStatus.EXPIRED
    blocked = {
        KrDayDecisionReasonCode.MARKET_GATE_BLOCKED,
        KrDayDecisionReasonCode.SPREAD_TOO_WIDE,
        KrDayDecisionReasonCode.STALE_EVIDENCE,
    }
    return (
        KrDayDecisionStatus.BLOCKED
        if blocked & set(reasons)
        else (KrDayDecisionStatus.REJECTED if reasons else KrDayDecisionStatus.INVESTIGATING)
    )


def _source_refs(request: KrDayCandidateAdmissionRequest, bars: tuple[KrCompletedMinuteBar, ...]) -> tuple[str, ...]:
    refs = [
        *(item.canonical_id for item in request.opportunity.evidence_refs),
        *(item.canonical_id for item in request.market.evidence_refs),
        *(item.evidence_ref.canonical_id for item in bars),
    ]
    return tuple(sorted(set(refs)))


def _bar_evidence(volume_ratio: str, price_response: str, value: str) -> tuple[KrDayDecisionEvidenceValue, ...]:
    return (
        KrDayDecisionEvidenceValue(name="completed_bar_volume_ratio", value=volume_ratio),
        KrDayDecisionEvidenceValue(name="completed_bar_price_response", value=price_response),
        KrDayDecisionEvidenceValue(name="completed_bar_trading_value_krw", value=value),
    )


def _thesis_key(at: dt.datetime, symbol: str, theme_name: str) -> str:
    material = "|".join((at.astimezone(_SEOUL).date().isoformat(), symbol, theme_name.strip().lower()))
    return hashlib.sha256(material.encode()).hexdigest()


def _integer(value: str | None) -> int | None:
    return int(value) if value is not None and value.isdecimal() else None


def _decimal(value: str | None) -> Decimal | None:
    try:
        parsed = Decimal(value) if value is not None else None
    except InvalidOperation:
        return None
    return parsed if parsed is not None and parsed.is_finite() else None


def _observed_feature_value(name: str, value: str | None) -> str:
    if value is None:
        return "missing"
    if name == "is_leader":
        return value if value in {"true", "false"} else "malformed"
    if name in {"theme_catalyst_count", "theme_publisher_count", "theme_related_symbol_count"}:
        return value if _integer(value) is not None else "malformed"
    if name in {"trading_value_krw", "volume_ratio"}:
        return value if _decimal(value) is not None else "malformed"
    return value if value.strip() else "malformed"


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "InvalidKrDayCandidateAdmissionError",
    "KrDayCandidateAdmissionPolicy",
    "KrDayCandidateAdmissionRequest",
    "KrDayCandidateAdmissionResult",
    "assess_kr_day_candidate_admission",
    "kr_day_candidate_thesis_key",
)

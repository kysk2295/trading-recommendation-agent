from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from typing import Final, assert_never, override

from trading_agent.intraday_feature_kernel import (
    FeatureSnapshotStatus,
    IntradayFeatureSnapshot,
)
from trading_agent.models import Recommendation
from trading_agent.research_identity_models import AgentFamily, MarketId
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.signal_contract_models import EvidenceRef, OpportunitySnapshot
from trading_agent.trade_signal_publication import (
    TradeSignalPublication,
    project_trade_signal_publications,
)
from trading_agent.us_equity_calendar import NEW_YORK
from trading_agent.us_feature_evidence_models import (
    EvidenceGatedSignalRequest,
    UsFeatureEvidenceBinding,
    UsFeatureGateBlocked,
    UsFeatureGateBlockedReason,
    UsFeatureGateReady,
    UsFeatureGateResult,
)
from trading_agent.us_intraday_volume_profile_models import (
    IntradayVolumeProfileError,
    validate_intraday_volume_profile,
)

_MAX_FEATURE_AGE: Final = dt.timedelta(minutes=2)
_FEATURE_NAMESPACE: Final = "research/intraday_feature"


class InvalidUsFeatureEvidenceProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "US feature evidence projection input is invalid"


def project_us_opportunity_with_feature_evidence(
    opportunity: OpportunitySnapshot,
    bindings: tuple[UsFeatureEvidenceBinding, ...],
    *,
    evaluated_at: dt.datetime,
) -> UsFeatureGateResult:
    _validate_gate_inputs(opportunity, bindings, evaluated_at)
    if evaluated_at >= opportunity.valid_until:
        return _blocked(
            opportunity,
            evaluated_at,
            UsFeatureGateBlockedReason.OPPORTUNITY_EXPIRED,
        )

    candidate_symbols = tuple(candidate.symbol for candidate in opportunity.candidates)
    binding_symbols = tuple(binding.symbol for binding in bindings)
    if not set(candidate_symbols).issubset(binding_symbols):
        return _blocked(
            opportunity,
            evaluated_at,
            UsFeatureGateBlockedReason.MISSING_EVIDENCE,
        )
    if set(candidate_symbols) != set(binding_symbols):
        return _blocked(
            opportunity,
            evaluated_at,
            UsFeatureGateBlockedReason.SYMBOL_COVERAGE,
        )

    feature_refs: list[EvidenceRef] = []
    for binding in sorted(bindings, key=lambda item: item.symbol):
        blocked_reason = _blocked_feature_reason(binding.snapshot, evaluated_at)
        if blocked_reason is not None:
            return _blocked(opportunity, evaluated_at, blocked_reason)
        feature_refs.append(_feature_evidence_ref(binding.snapshot))

    evidence_refs = tuple(sorted((*opportunity.evidence_refs, *feature_refs), key=lambda item: item.canonical_id))
    evidence_ids = tuple(item.canonical_id for item in evidence_refs)
    if len(evidence_ids) != len(set(evidence_ids)):
        raise InvalidUsFeatureEvidenceProjectionError
    derived_id = _derived_opportunity_id(opportunity, evidence_refs, evaluated_at)
    derived = OpportunitySnapshot(
        opportunity_id=derived_id,
        strategy_lane=opportunity.strategy_lane,
        producer_strategy_version=opportunity.producer_strategy_version,
        observed_at=evaluated_at,
        valid_until=opportunity.valid_until,
        candidates=opportunity.candidates,
        evidence_refs=evidence_refs,
        source_coverage=opportunity.source_coverage,
    )
    return UsFeatureGateReady(derived)


def project_evidence_gated_trade_signal_publications(
    gate: UsFeatureGateResult,
    recommendations: tuple[Recommendation, ...],
    request: EvidenceGatedSignalRequest,
) -> tuple[TradeSignalPublication, ...]:
    if type(request) is not EvidenceGatedSignalRequest or type(recommendations) is not tuple:
        raise InvalidUsFeatureEvidenceProjectionError
    match gate:
        case UsFeatureGateBlocked():
            return ()
        case UsFeatureGateReady(opportunity=opportunity):
            return project_trade_signal_publications(
                recommendations,
                strategy_lane=request.strategy_lane,
                strategy_version=request.strategy_version,
                opportunity=opportunity,
                published_at=request.published_at,
                created_after=request.created_after,
            )
        case unreachable:
            assert_never(unreachable)


def _validate_gate_inputs(
    opportunity: OpportunitySnapshot,
    bindings: tuple[UsFeatureEvidenceBinding, ...],
    evaluated_at: dt.datetime,
) -> None:
    if (
        type(opportunity) is not OpportunitySnapshot
        or type(bindings) is not tuple
        or not _aware(evaluated_at)
        or opportunity.strategy_lane.market_id is not MarketId.US_EQUITIES
        or opportunity.strategy_lane.agent_family is not AgentFamily.OPPORTUNITY_MANAGER
        or opportunity.observed_at > evaluated_at
    ):
        raise InvalidUsFeatureEvidenceProjectionError
    symbols: list[str] = []
    instrument_ids: list[str] = []
    for binding in bindings:
        if (
            type(binding) is not UsFeatureEvidenceBinding
            or not binding.symbol
            or type(binding.snapshot) is not IntradayFeatureSnapshot
        ):
            raise InvalidUsFeatureEvidenceProjectionError
        symbols.append(binding.symbol)
        instrument_ids.append(binding.snapshot.instrument_id)
    if len(symbols) != len(set(symbols)) or len(instrument_ids) != len(set(instrument_ids)):
        raise InvalidUsFeatureEvidenceProjectionError


def _blocked_feature_reason(
    snapshot: IntradayFeatureSnapshot,
    evaluated_at: dt.datetime,
) -> UsFeatureGateBlockedReason | None:
    match snapshot.status:
        case FeatureSnapshotStatus.BLOCKED_GAP:
            return UsFeatureGateBlockedReason.FEATURE_GAP
        case FeatureSnapshotStatus.BLOCKED_STALE:
            return UsFeatureGateBlockedReason.FEATURE_STALE
        case FeatureSnapshotStatus.BLOCKED_INSUFFICIENT_HISTORY:
            return UsFeatureGateBlockedReason.INSUFFICIENT_HISTORY
        case FeatureSnapshotStatus.READY:
            pass
        case unreachable:
            assert_never(unreachable)
    if snapshot.observed_at > evaluated_at:
        return UsFeatureGateBlockedReason.NONCAUSAL_EVIDENCE
    if evaluated_at - snapshot.observed_at > _MAX_FEATURE_AGE:
        return UsFeatureGateBlockedReason.FEATURE_STALE
    _validate_ready_snapshot(snapshot)
    return None


def _validate_ready_snapshot(snapshot: IntradayFeatureSnapshot) -> None:
    try:
        validate_intraday_volume_profile(snapshot.volume_profile)
    except IntradayVolumeProfileError:
        raise InvalidUsFeatureEvidenceProjectionError from None
    values = (
        snapshot.vwap,
        snapshot.atr14,
        snapshot.rsi14,
        snapshot.macd_line,
        snapshot.macd_signal,
        snapshot.macd_histogram,
        snapshot.rvol,
    )
    if (
        type(snapshot.identity) is not ResearchInputIdentity
        or not snapshot.instrument_id
        or snapshot.volume_profile.instrument_id != snapshot.instrument_id
        or not _aware(snapshot.observed_at)
        or snapshot.volume_profile.target_session_date != snapshot.observed_at.astimezone(NEW_YORK).date()
        or snapshot.source_start_at is None
        or snapshot.source_end_at is None
        or not _aware(snapshot.source_start_at)
        or not _aware(snapshot.source_end_at)
        or snapshot.source_start_at >= snapshot.source_end_at
        or snapshot.source_end_at >= snapshot.observed_at
        or snapshot.bar_count < 35
        or not snapshot.indicator_semantic_version
        or any(type(value) is not Decimal or not value.is_finite() for value in values)
        or type(snapshot.breakout_close_above_prior_high) is not bool
    ):
        raise InvalidUsFeatureEvidenceProjectionError


def _feature_evidence_ref(snapshot: IntradayFeatureSnapshot) -> EvidenceRef:
    source_start_at = snapshot.source_start_at
    source_end_at = snapshot.source_end_at
    if source_start_at is None or source_end_at is None:
        raise InvalidUsFeatureEvidenceProjectionError
    payload = {
        "atr14": str(snapshot.atr14),
        "bar_count": snapshot.bar_count,
        "breakout": snapshot.breakout_close_above_prior_high,
        "identity_sha256": snapshot.identity.identity_sha256,
        "indicator_semantic_version": snapshot.indicator_semantic_version,
        "instrument_id": snapshot.instrument_id,
        "macd_histogram": str(snapshot.macd_histogram),
        "macd_line": str(snapshot.macd_line),
        "macd_signal": str(snapshot.macd_signal),
        "observed_at": snapshot.observed_at.isoformat(),
        "rsi14": str(snapshot.rsi14),
        "rvol": str(snapshot.rvol),
        "source_end_at": source_end_at.isoformat(),
        "source_start_at": source_start_at.isoformat(),
        "vwap": str(snapshot.vwap),
        "volume_profile_evidence_sha256": snapshot.volume_profile.evidence_sha256,
        "volume_profile_expected_cumulative_volume": str(snapshot.volume_profile.expected_cumulative_volume),
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return EvidenceRef(
        namespace=_FEATURE_NAMESPACE,
        record_id=hashlib.sha256(encoded.encode()).hexdigest(),
        observed_at=snapshot.observed_at,
    )


def _derived_opportunity_id(
    opportunity: OpportunitySnapshot,
    evidence_refs: tuple[EvidenceRef, ...],
    evaluated_at: dt.datetime,
) -> str:
    coordinates = "|".join(
        (
            opportunity.model_dump_json(),
            evaluated_at.isoformat(),
            *(item.canonical_id for item in evidence_refs),
        )
    )
    return f"us-m4-{hashlib.sha256(coordinates.encode()).hexdigest()}"


def _blocked(
    opportunity: OpportunitySnapshot,
    evaluated_at: dt.datetime,
    reason: UsFeatureGateBlockedReason,
) -> UsFeatureGateBlocked:
    return UsFeatureGateBlocked(reason, opportunity.opportunity_id, evaluated_at)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "EvidenceGatedSignalRequest",
    "InvalidUsFeatureEvidenceProjectionError",
    "UsFeatureEvidenceBinding",
    "UsFeatureGateBlocked",
    "UsFeatureGateBlockedReason",
    "UsFeatureGateReady",
    "project_evidence_gated_trade_signal_publications",
    "project_us_opportunity_with_feature_evidence",
)

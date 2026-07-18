from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Final, override

from trading_agent.canonical_event_models import CanonicalEventEnvelope
from trading_agent.intraday_feature_kernel import IntradayFeatureSnapshot
from trading_agent.research_evidence_models import (
    ClaimStance,
    ExtractionMethod,
    JsonValue,
    ResearchClaimExtraction,
)
from trading_agent.us_sip_typed_feature_validation import (
    UsSipTypedFeatureValidationError,
    validate_us_sip_typed_feature_input,
)

_EXTRACTOR_VERSION: Final = "us-sip-typed-features-v1"


class UsSipTypedFeatureExtractionError(ValueError):
    def __init__(self) -> None:
        super().__init__("US SIP typed feature extraction is blocked")

    @override
    def __str__(self) -> str:
        return "US SIP typed feature extraction is blocked"

    @override
    def __repr__(self) -> str:
        return "UsSipTypedFeatureExtractionError()"


def extract_us_sip_typed_feature_claims(
    snapshot: IntradayFeatureSnapshot,
    dataset_directory: Path,
    *,
    minimum_rvol_bps: int,
) -> tuple[ResearchClaimExtraction, ...]:
    try:
        trigger = validate_us_sip_typed_feature_input(snapshot, dataset_directory, minimum_rvol_bps)
        base = _feature_payload(snapshot, trigger, minimum_rvol_bps)
        return (
            _claim(
                snapshot,
                trigger,
                base,
                claim_key="us.intraday.breakout.close_above_prior_high",
                claim_kind="technical.breakout",
                stance=_stance(_boolean(snapshot.breakout_close_above_prior_high)),
            ),
            _claim(
                snapshot,
                trigger,
                base,
                claim_key=f"us.intraday.rvol.gte.{minimum_rvol_bps}bps",
                claim_kind="technical.rvol_threshold",
                stance=_stance(_decimal(snapshot.rvol) * Decimal(10_000) >= minimum_rvol_bps),
            ),
        )
    except (
        AttributeError,
        OSError,
        TypeError,
        UsSipTypedFeatureValidationError,
        ValueError,
    ):
        raise UsSipTypedFeatureExtractionError from None


def _feature_payload(
    snapshot: IntradayFeatureSnapshot,
    event: CanonicalEventEnvelope,
    minimum_rvol_bps: int,
) -> dict[str, JsonValue]:
    return {
        "bar_count": snapshot.bar_count,
        "breakout_close_above_prior_high": snapshot.breakout_close_above_prior_high,
        "event_content_hash": event.content_hash,
        "event_id": event.event_id,
        "indicator_semantic_version": snapshot.indicator_semantic_version,
        "instrument_id": snapshot.instrument_id,
        "minimum_rvol_bps": minimum_rvol_bps,
        "observed_at": snapshot.observed_at.isoformat(),
        "research_input_identity_sha256": snapshot.identity.identity_sha256,
        "source_end_at": _timestamp(snapshot.source_end_at),
        "source_start_at": _timestamp(snapshot.source_start_at),
        "typed_indicators": {
            "atr14": str(snapshot.atr14),
            "macd_histogram": str(snapshot.macd_histogram),
            "macd_line": str(snapshot.macd_line),
            "macd_signal": str(snapshot.macd_signal),
            "rsi14": str(snapshot.rsi14),
            "rvol": str(snapshot.rvol),
            "vwap": str(snapshot.vwap),
        },
        "volume_profile_evidence_sha256": snapshot.volume_profile.evidence_sha256,
    }


def _claim(
    snapshot: IntradayFeatureSnapshot,
    event: CanonicalEventEnvelope,
    base: dict[str, JsonValue],
    *,
    claim_key: str,
    claim_kind: str,
    stance: ClaimStance,
) -> ResearchClaimExtraction:
    output_sha256 = _sha256({**base, "claim_key": claim_key, "stance": stance.value})
    return ResearchClaimExtraction(
        event_id=event.event_id,
        event_content_hash=event.content_hash,
        source_id=event.source_id,
        raw_receipt_ref=event.raw_receipt_ref,
        entity_refs=event.entity_refs,
        claim_key=claim_key,
        claim_kind=claim_kind,
        stance=stance,
        confidence_bps=10_000,
        extracted_at=snapshot.observed_at,
        extraction_method=ExtractionMethod.DETERMINISTIC,
        extractor_version=_EXTRACTOR_VERSION,
        model_version=None,
        prompt_version=None,
        output_sha256=output_sha256,
    )


def _stance(value: bool) -> ClaimStance:
    return ClaimStance.SUPPORTS if value else ClaimStance.DISPUTES


def _sha256(payload: JsonValue) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _timestamp(value: dt.datetime | None) -> str:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        raise UsSipTypedFeatureExtractionError
    return value.isoformat()


def _decimal(value: Decimal | None) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise UsSipTypedFeatureExtractionError
    return value


def _boolean(value: bool | None) -> bool:
    if type(value) is not bool:
        raise UsSipTypedFeatureExtractionError
    return value


__all__ = (
    "UsSipTypedFeatureExtractionError",
    "extract_us_sip_typed_feature_claims",
)

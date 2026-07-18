from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final, override

from trading_agent.canonical_event_models import (
    CanonicalEntityRef,
    CanonicalEntityType,
    CanonicalEventEnvelope,
    CanonicalEventOperation,
)
from trading_agent.research_evidence_models import (
    ClaimStance,
    ExtractionMethod,
    JsonValue,
    ResearchClaimExtraction,
)
from trading_agent.signal_contract_models import OpportunityCandidate
from trading_agent.us_scanner_research_source import UsScannerResearchSource

_EXTRACTOR_VERSION: Final = "us-scanner-candidate-v1"
_SOURCE_ID: Final = "internal/us_opportunity"


class UsScannerCandidateExtractionError(ValueError):
    @override
    def __str__(self) -> str:
        return "US scanner candidate extraction is blocked"


@dataclass(frozen=True, slots=True)
class _CandidateInput:
    candidate: OpportunityCandidate
    instrument_id: str
    canonical_payload: bytes


def extract_us_scanner_candidate_claims(
    source: UsScannerResearchSource,
) -> tuple[ResearchClaimExtraction, ...]:
    try:
        opportunity = source.opportunity
        event_by_rank = {_rank(event): event for event in source.events}
        if len(event_by_rank) != len(source.events):
            raise UsScannerCandidateExtractionError
        claims = []
        for candidate, scanned in zip(
            opportunity.candidates,
            source.snapshot.candidates,
            strict=True,
        ):
            event = event_by_rank[candidate.rank]
            candidate_input = _CandidateInput(
                candidate,
                scanned.instrument_id,
                _candidate_payload(source, candidate, scanned.instrument_id),
            )
            _validate_event(source, event, candidate_input)
            claims.append(_claim(source, event, candidate_input.canonical_payload))
        return tuple(claims)
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise UsScannerCandidateExtractionError from None


def _candidate_payload(
    source: UsScannerResearchSource,
    candidate: OpportunityCandidate,
    instrument_id: str,
) -> bytes:
    payload: dict[str, JsonValue] = {
        "candidate": candidate.model_dump(mode="json"),
        "foundation_id": source.foundation.manifest_id,
        "instrument_id": instrument_id,
    }
    if source.security_master_id is not None:
        payload["security_master_id"] = source.security_master_id
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _validate_event(
    source: UsScannerResearchSource,
    event: CanonicalEventEnvelope,
    candidate_input: _CandidateInput,
) -> None:
    opportunity = source.opportunity
    rank = candidate_input.candidate.rank
    content_hash = hashlib.sha256(candidate_input.canonical_payload).hexdigest()
    entity = CanonicalEntityRef(
        entity_type=CanonicalEntityType.INSTRUMENT,
        entity_id=candidate_input.instrument_id,
    )
    if (
        type(event) is not CanonicalEventEnvelope
        or event.event_id != f"scanner-candidate-{rank:04d}-{content_hash[:16]}"
        or event.source_id.canonical_id != _SOURCE_ID
        or event.provider_event_id != f"{opportunity.opportunity_id}:{rank}:{source.foundation.manifest_id}"
        or event.entity_refs != (entity,)
        or event.event_type != "scanner_candidate"
        or event.event_time != opportunity.observed_at
        or event.received_at != opportunity.observed_at
        or event.normalized_at != opportunity.observed_at
        or event.sequence_or_offset != str(rank)
        or event.operation is not CanonicalEventOperation.ORIGINAL
        or event.correction_of is not None
        or event.raw_receipt_ref != source.raw_receipt_ref
        or event.content_hash != content_hash
    ):
        raise UsScannerCandidateExtractionError


def _claim(
    source: UsScannerResearchSource,
    event: CanonicalEventEnvelope,
    canonical_payload: bytes,
) -> ResearchClaimExtraction:
    output: dict[str, JsonValue] = {
        "candidate_payload_sha256": hashlib.sha256(canonical_payload).hexdigest(),
        "evidence_refs": [item.model_dump(mode="json") for item in source.opportunity.evidence_refs],
        "event_content_hash": event.content_hash,
        "event_id": event.event_id,
        "research_input_identity_sha256": source.snapshot.identity.identity_sha256,
        "source_coverage": [item.model_dump(mode="json") for item in source.opportunity.source_coverage],
    }
    return ResearchClaimExtraction(
        event_id=event.event_id,
        event_content_hash=event.content_hash,
        source_id=event.source_id,
        raw_receipt_ref=event.raw_receipt_ref,
        entity_refs=event.entity_refs,
        claim_key="us.scanner.ranking_momentum.selected",
        claim_kind="scanner.candidate_selection",
        stance=ClaimStance.REPORTS,
        confidence_bps=10_000,
        extracted_at=source.opportunity.observed_at,
        extraction_method=ExtractionMethod.DETERMINISTIC,
        extractor_version=_EXTRACTOR_VERSION,
        model_version=None,
        prompt_version=None,
        output_sha256=_sha256(output),
    )


def _rank(event: CanonicalEventEnvelope) -> int:
    if event.sequence_or_offset is None:
        raise UsScannerCandidateExtractionError
    rank = int(event.sequence_or_offset)
    if rank <= 0 or str(rank) != event.sequence_or_offset:
        raise UsScannerCandidateExtractionError
    return rank


def _sha256(payload: JsonValue) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


__all__ = (
    "UsScannerCandidateExtractionError",
    "extract_us_scanner_candidate_claims",
)

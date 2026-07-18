from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import override

from pydantic import ValidationError

from trading_agent.canonical_event_history import (
    CanonicalEventHistoryError,
    active_canonical_events_as_of,
)
from trading_agent.canonical_event_models import (
    CanonicalEventEnvelope,
    CanonicalEventOperation,
)
from trading_agent.research_evidence_models import (
    ClaimCorroborationStatus,
    ClaimNoveltyStatus,
    ClaimStance,
    ResearchClaimExtraction,
    ResearchClaimSnapshot,
    ResearchEvidenceReadModel,
    content_sha256,
)


class ResearchEvidenceReadModelError(ValueError):
    @override
    def __str__(self) -> str:
        return "research evidence read model input is invalid"


def build_research_evidence_read_model(
    events: tuple[CanonicalEventEnvelope, ...],
    extractions: tuple[ResearchClaimExtraction, ...],
    *,
    as_of: dt.datetime,
    current_window: dt.timedelta,
    baseline_window: dt.timedelta,
    burst_threshold_bps: int,
) -> ResearchEvidenceReadModel:
    try:
        checked_events = tuple(CanonicalEventEnvelope.model_validate(item.model_dump(mode="python")) for item in events)
        checked_extractions = tuple(
            ResearchClaimExtraction.model_validate(item.model_dump(mode="python")) for item in extractions
        )
        _validate_request(
            checked_events,
            checked_extractions,
            as_of,
            current_window,
            baseline_window,
            burst_threshold_bps,
        )
        active_events = active_canonical_events_as_of(checked_events, as_of=as_of)
        by_event = {item.event_id: item for item in active_events}
        for extraction in checked_extractions:
            _validate_lineage(extraction, by_event[extraction.event_id], as_of)
        claims = _claims(
            checked_extractions,
            by_event,
            as_of=as_of,
            current_window=current_window,
            baseline_window=baseline_window,
            burst_threshold_bps=burst_threshold_bps,
        )
        provisional = ResearchEvidenceReadModel.model_construct(
            as_of=as_of,
            baseline_window_seconds=int(baseline_window.total_seconds()),
            burst_threshold_bps=burst_threshold_bps,
            claims=claims,
            current_window_seconds=int(current_window.total_seconds()),
            extraction_count=len(checked_extractions),
            source_event_count=sum(event.normalized_at <= as_of for event in checked_events),
            content_sha256="0" * 64,
        )
        normalized = provisional.model_dump(mode="json")
        _ = normalized.pop("content_sha256")
        return ResearchEvidenceReadModel(**normalized, content_sha256=content_sha256(normalized))
    except (
        CanonicalEventHistoryError,
        KeyError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise ResearchEvidenceReadModelError from None


def _validate_request(
    events: tuple[CanonicalEventEnvelope, ...],
    extractions: tuple[ResearchClaimExtraction, ...],
    as_of: dt.datetime,
    current_window: dt.timedelta,
    baseline_window: dt.timedelta,
    burst_threshold_bps: int,
) -> None:
    event_ids = tuple(item.event_id for item in events)
    evidence_ids = tuple(item.evidence_id for item in extractions)
    current_seconds = current_window.total_seconds()
    baseline_seconds = baseline_window.total_seconds()
    if (
        not events
        or not extractions
        or len(event_ids) != len(set(event_ids))
        or len(evidence_ids) != len(set(evidence_ids))
        or as_of.tzinfo is None
        or as_of.utcoffset() is None
        or not 1 <= current_seconds <= 86_400
        or not current_seconds <= baseline_seconds <= 2_592_000
        or not 10_000 <= burst_threshold_bps <= 100_000
    ):
        raise ValueError


def _validate_lineage(
    extraction: ResearchClaimExtraction,
    event: CanonicalEventEnvelope,
    as_of: dt.datetime,
) -> None:
    if (
        event.operation is CanonicalEventOperation.TOMBSTONE
        or event.normalized_at > as_of
        or extraction.extracted_at < event.normalized_at
        or extraction.extracted_at > as_of
        or extraction.event_content_hash != event.content_hash
        or extraction.source_id != event.source_id
        or extraction.raw_receipt_ref != event.raw_receipt_ref
        or extraction.entity_refs != event.entity_refs
    ):
        raise ValueError


def _claims(
    extractions: tuple[ResearchClaimExtraction, ...],
    events: dict[str, CanonicalEventEnvelope],
    *,
    as_of: dt.datetime,
    current_window: dt.timedelta,
    baseline_window: dt.timedelta,
    burst_threshold_bps: int,
) -> tuple[ResearchClaimSnapshot, ...]:
    grouped: dict[tuple[str, tuple[str, ...]], list[ResearchClaimExtraction]] = defaultdict(list)
    for extraction in extractions:
        key = (extraction.claim_key, tuple(item.canonical_id for item in extraction.entity_refs))
        grouped[key].append(extraction)
    claims = tuple(
        snapshot
        for rows in grouped.values()
        if (
            snapshot := _claim_snapshot(
                tuple(rows),
                events,
                as_of,
                current_window,
                baseline_window,
                burst_threshold_bps,
            )
        )
        is not None
    )
    if not claims:
        raise ValueError
    return tuple(sorted(claims, key=lambda item: item.claim_snapshot_id))


def _claim_snapshot(
    rows: tuple[ResearchClaimExtraction, ...],
    events: dict[str, CanonicalEventEnvelope],
    as_of: dt.datetime,
    current_window: dt.timedelta,
    baseline_window: dt.timedelta,
    burst_threshold_bps: int,
) -> ResearchClaimSnapshot | None:
    current_start = as_of - current_window
    baseline_start = current_start - baseline_window
    current = tuple(item for item in rows if current_start < events[item.event_id].received_at <= as_of)
    if not current:
        return None
    baseline = tuple(item for item in rows if baseline_start < events[item.event_id].received_at <= current_start)
    kinds = {item.claim_kind for item in rows}
    if len(kinds) != 1:
        raise ValueError
    sources = tuple(sorted({item.source_id.canonical_id for item in current}))
    stance_counts = {stance: sum(item.stance is stance for item in current) for stance in ClaimStance}
    evidence_ids = tuple(sorted(item.evidence_id for item in current))
    baseline_evidence_ids = tuple(sorted(item.evidence_id for item in baseline))
    observed = tuple(events[item.event_id].received_at for item in current)
    provisional = ResearchClaimSnapshot.model_construct(
        claim_snapshot_id="0" * 64,
        claim_key=current[0].claim_key,
        claim_kind=current[0].claim_kind,
        entity_refs=current[0].entity_refs,
        evidence_ids=evidence_ids,
        baseline_evidence_ids=baseline_evidence_ids,
        source_ids=sources,
        first_observed_at=min(observed),
        latest_observed_at=max(observed),
        current_evidence_count=len(current),
        baseline_evidence_count=len(baseline),
        independent_source_count=len(sources),
        supporting_evidence_count=stance_counts[ClaimStance.SUPPORTS],
        disputing_evidence_count=stance_counts[ClaimStance.DISPUTES],
        reporting_evidence_count=stance_counts[ClaimStance.REPORTS],
        speculative_evidence_count=stance_counts[ClaimStance.SPECULATIVE],
        minimum_confidence_bps=min(item.confidence_bps for item in current),
        corroboration_status=_corroboration(stance_counts, len(sources)),
        novelty_status=_novelty(
            len(current),
            len(baseline),
            current_window,
            baseline_window,
            burst_threshold_bps,
        ),
    )
    normalized = provisional.model_dump(mode="json")
    _ = normalized.pop("claim_snapshot_id")
    return ResearchClaimSnapshot(
        claim_snapshot_id=content_sha256(normalized),
        **normalized,
    )


def _corroboration(
    counts: dict[ClaimStance, int],
    source_count: int,
) -> ClaimCorroborationStatus:
    asserted = counts[ClaimStance.SUPPORTS] + counts[ClaimStance.REPORTS]
    if asserted and counts[ClaimStance.DISPUTES]:
        return ClaimCorroborationStatus.CONFLICTED
    if source_count >= 2 and asserted >= 2:
        return ClaimCorroborationStatus.CORROBORATED
    return ClaimCorroborationStatus.UNCONFIRMED


def _novelty(
    current_count: int,
    baseline_count: int,
    current_window: dt.timedelta,
    baseline_window: dt.timedelta,
    threshold_bps: int,
) -> ClaimNoveltyStatus:
    if baseline_count == 0:
        return ClaimNoveltyStatus.NOVEL
    current_rate = current_count * int(baseline_window.total_seconds()) * 10_000
    threshold_rate = baseline_count * int(current_window.total_seconds()) * threshold_bps
    return ClaimNoveltyStatus.BURST if current_rate >= threshold_rate else ClaimNoveltyStatus.RECURRING


__all__ = (
    "ResearchEvidenceReadModelError",
    "build_research_evidence_read_model",
)

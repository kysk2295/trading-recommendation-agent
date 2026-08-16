from __future__ import annotations

import hashlib
from typing import Final

from trading_agent.dashboard_outbound_redaction import redact_outbound_text, require_safe_outbound_text
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import (
    HermesProjectionRecord,
    HermesProjectionResult,
    project_outcomes,
)
from trading_agent.hermes_delivery_store import HermesDeliveryWriter
from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1, ResearchAgentResultV1

_NO_PROJECTED_RESULT_IDS: Final[frozenset[str]] = frozenset()


def project_research_agent_results(
    results: tuple[ResearchAgentResultV1, ...],
    writer: HermesDeliveryWriter,
    *,
    evidence: tuple[ResearchAgentEvidenceV1, ...] = (),
    projected_result_ids: frozenset[str] = _NO_PROJECTED_RESULT_IDS,
) -> HermesProjectionResult:
    records = tuple(
        _projection_record(result, evidence)
        for result in results
        if result.result_id not in projected_result_ids
    )
    return project_outcomes(records, writer)


def render_research_agent_result(
    result: ResearchAgentResultV1,
    evidence: tuple[ResearchAgentEvidenceV1, ...] = (),
) -> str:
    resolved = next(
        (
            item
            for item in evidence
            if item.agent_family_id == result.agent_family_id and item.payload_sha256 in result.artifact_refs
        ),
        None,
    )
    artifact = "none" if resolved is None else resolved.payload_sha256
    next_wake = (
        result.next_wake_kind.value
        if result.next_wake_at is None
        else f"{result.next_wake_kind.value}@{result.next_wake_at.isoformat()}"
    )
    text = redact_outbound_text(
        f"{result.agent_family_id}: {result.summary} Status: {result.status.value}. "
        f"artifact={artifact}. next wake={next_wake}. order authority: false.",
        max_chars=4096,
    ).strip()
    require_safe_outbound_text(text)
    return text


def _projection_record(
    result: ResearchAgentResultV1,
    evidence: tuple[ResearchAgentEvidenceV1, ...],
) -> HermesProjectionRecord:
    validated = result
    return HermesProjectionRecord(
        source_event_id=validated.result_id,
        root_source_event_id=None,
        kind=HermesDeliveryKind.RESEARCH,
        market_id=validated.market_id,
        agent_family=validated.agent_family_id,
        lane_id=None,
        strategy_version=None,
        instrument_id=None,
        occurred_at=validated.occurred_at,
        status=validated.status.value,
        evidence_refs=validated.evidence_refs,
        rendered_text=render_research_agent_result(validated, evidence),
        payload_sha256=hashlib.sha256(validated.model_dump_json(exclude_unset=True).encode()).hexdigest(),
    )


__all__ = (
    "project_research_agent_results",
    "render_research_agent_result",
)

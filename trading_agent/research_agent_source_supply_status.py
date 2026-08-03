from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.dashboard_projection_derivatives import project_derivatives
from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1
from trading_agent.research_agent_service_config import ResearchAgentServiceConfig
from trading_agent.research_agent_source_supply import (
    InvalidMarketContextSupplyError,
    MarketContextSupplyUnavailableError,
    materialize_current_market_context,
    prepare_current_market_context,
)
from trading_agent.research_agent_sources import (
    ADAPTER_FAMILIES,
    ResearchAgentSourceCollectionBatch,
    ResearchAgentSourceFailure,
    collect_research_agent_evidence_isolated,
)
from trading_agent.research_agent_systematic_input_models import (
    BlockedSystematicInputActivation,
    ReadySystematicInputActivation,
)
from trading_agent.research_agent_systematic_input_store import (
    InvalidSystematicInputActivationError,
    load_systematic_input_activation,
)
from trading_agent.us_equity_calendar import NEW_YORK, next_regular_session, regular_session_bounds

SupplyState = Literal["ready", "waiting_session", "collecting", "operator_action_required", "blocked"]
_DIGEST_CAP: Final = 8
_PRECEDENCE: Final[dict[SupplyState, int]] = {
    "ready": 0,
    "waiting_session": 1,
    "collecting": 2,
    "operator_action_required": 3,
    "blocked": 4,
}


@dataclass(frozen=True, slots=True)
class _FamilyContext:
    config: ResearchAgentServiceConfig
    now: dt.datetime
    family: AgentFamilyId
    batch: ResearchAgentSourceCollectionBatch
    market_context_supply_ready: bool


@dataclass(frozen=True, slots=True)
class _FamilyEvidence:
    family: AgentFamilyId
    evidence: tuple[ResearchAgentEvidenceV1, ...]
    provenance: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Classification:
    reason: str
    next_action: str


class FamilySourceSupplyStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    agent_family_id: AgentFamilyId
    state: SupplyState
    reason: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{2,127}$")
    next_action: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:+-]{2,255}$")
    evidence_count: int = Field(ge=0, le=96)
    provenance_sha256: tuple[str, ...] = Field(max_length=_DIGEST_CAP)


class ResearchAgentSourceSupplyStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    state: SupplyState
    inspected_at: dt.datetime
    materialized_market_context: bool
    families: tuple[
        FamilySourceSupplyStatus,
        FamilySourceSupplyStatus,
        FamilySourceSupplyStatus,
        FamilySourceSupplyStatus,
        FamilySourceSupplyStatus,
        FamilySourceSupplyStatus,
    ]
    provider_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    heavy_processes: Literal[0] = 0
    network_calls: Literal[0] = 0
    broker_mutation: Literal[0] = 0
    order_authority_mutation: Literal[0] = 0
    allocation_mutation: Literal[0] = 0


def inspect_source_supply(
    config: ResearchAgentServiceConfig,
    now: dt.datetime,
    materialize: bool,
) -> ResearchAgentSourceSupplyStatus:
    supply_failure: ResearchAgentSourceFailure | None = None
    supply_ready = False
    created = False
    try:
        if materialize:
            created = materialize_current_market_context(config.source_paths, now).created
        else:
            _ = prepare_current_market_context(config.source_paths, now)
        supply_ready = True
    except MarketContextSupplyUnavailableError as unavailable:
        _ = unavailable.reason
    except InvalidMarketContextSupplyError as error:
        supply_failure = ResearchAgentSourceFailure("market_context", error.reason, now)
    batch = collect_research_agent_evidence_isolated(config.source_paths, now=now)
    if supply_failure is not None:
        batch = ResearchAgentSourceCollectionBatch(
            tuple(item for item in batch.evidence if item.agent_family_id != "market_context"),
            (*tuple(item for item in batch.failures if item.agent_family_id != "market_context"), supply_failure),
        )
    grouped = (
        _family(_FamilyContext(config, now, ADAPTER_FAMILIES[0], batch, supply_ready)),
        _family(_FamilyContext(config, now, ADAPTER_FAMILIES[1], batch, supply_ready)),
        _family(_FamilyContext(config, now, ADAPTER_FAMILIES[2], batch, supply_ready)),
        _family(_FamilyContext(config, now, ADAPTER_FAMILIES[3], batch, supply_ready)),
        _family(_FamilyContext(config, now, ADAPTER_FAMILIES[4], batch, supply_ready)),
        _family(_FamilyContext(config, now, ADAPTER_FAMILIES[5], batch, supply_ready)),
    )
    state = max(grouped, key=lambda item: _PRECEDENCE[item.state]).state
    return ResearchAgentSourceSupplyStatus(
        state=state,
        inspected_at=now,
        materialized_market_context=created,
        families=grouped,
    )


def canonical_source_supply_json(status: ResearchAgentSourceSupplyStatus) -> str:
    return json.dumps(status.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _family(context: _FamilyContext) -> FamilySourceSupplyStatus:
    family = context.family
    evidence = tuple(item for item in context.batch.evidence if item.agent_family_id == family)
    failure = next((item for item in context.batch.failures if item.agent_family_id == family), None)
    provenance = tuple(sorted({digest for item in evidence for digest in item.evidence_refs}))[:_DIGEST_CAP]
    facts = _FamilyEvidence(family, evidence, provenance)
    if failure is not None:
        return _status(
            _FamilyEvidence(family, (), ()),
            "blocked",
            _Classification(failure.reason, "inspect_private_source_integrity"),
        )
    if family == "systematic_quant":
        systematic = _systematic(context.config.systematic.input_activation, facts)
        if systematic is not None:
            return systematic
    if family == "derivatives_research":
        return _derivatives(context.config.source_paths.outputs_root, context.now, facts)
    blocked = next((item for item in evidence if ".blocked." in item.source_key), None)
    if blocked is None:
        return _status(facts, "ready", _Classification("local_source_ready", "continue_local_collection"))
    reason = blocked.source_key.rsplit(".blocked.", maxsplit=1)[1]
    if reason == "session_closed":
        return _status(facts, "waiting_session", _Classification(reason, _next_session_action(context.now)))
    if family == "market_context" and context.market_context_supply_ready:
        return _status(
            facts,
            "collecting",
            _Classification("market_context_tick_required", "run_source_supply_tick"),
        )
    if family == "swing_trading" and reason in {"shadow_ledger_unavailable", "shadow_evidence_empty"}:
        return _status(facts, "operator_action_required", _Classification(reason, "run_post_close_swing_shadow_work"))
    return _status(facts, "blocked", _Classification(reason, f"repair_{family}_source"))


def _systematic(
    path: Path,
    facts: _FamilyEvidence,
) -> FamilySourceSupplyStatus | None:
    if not path.exists():
        return _status(
            facts,
            "operator_action_required",
            _Classification("activation_unavailable", "create_bounded_systematic_activation"),
        )
    try:
        activation = load_systematic_input_activation(path)
    except InvalidSystematicInputActivationError:
        return _status(facts, "blocked", _Classification("activation_invalid", "repair_systematic_activation"))
    match activation:
        case BlockedSystematicInputActivation(reason_code="minimum_clean_sessions_not_met"):
            return _status(
                facts,
                "collecting",
                _Classification("minimum_clean_sessions_not_met", "continue_clean_session_collection"),
            )
        case BlockedSystematicInputActivation(reason_code=reason):
            return _status(
                facts, "operator_action_required", _Classification(reason, "repair_systematic_activation_input")
            )
        case ReadySystematicInputActivation():
            return None
        case unreachable:
            assert_never(unreachable)


def _derivatives(
    outputs: Path,
    now: dt.datetime,
    facts: _FamilyEvidence,
) -> FamilySourceSupplyStatus:
    projection = project_derivatives(outputs, now=now).workspace
    if projection.state in {"corrupt", "error"}:
        return _status(
            facts, "blocked", _Classification("derivatives_source_invalid", "repair_derivatives_source_integrity")
        )
    if projection.projected_count > 0 and projection.blocker_code == "current_quote_not_licensed":
        return _status(
            facts,
            "ready",
            _Classification("research_shadow_available_realtime_entitlement_missing", "continue_research_shadow_only"),
        )
    if projection.blocker_code == "options_entitlement_missing":
        return _status(
            facts,
            "operator_action_required",
            _Classification(
                "external_realtime_entitlement_unverified", "obtain_reviewed_derivatives_research_entitlement"
            ),
        )
    if projection.blocker_code is not None:
        return _status(facts, "blocked", _Classification(projection.blocker_code, "repair_derivatives_research_source"))
    return _status(facts, "ready", _Classification("reviewed_research_source_ready", "continue_research_shadow_only"))


def _status(
    facts: _FamilyEvidence,
    state: SupplyState,
    classification: _Classification,
) -> FamilySourceSupplyStatus:
    return FamilySourceSupplyStatus(
        agent_family_id=facts.family,
        state=state,
        reason=classification.reason,
        next_action=classification.next_action,
        evidence_count=len(facts.evidence),
        provenance_sha256=facts.provenance,
    )


def _next_session_action(now: dt.datetime) -> str:
    current = now.astimezone(NEW_YORK)
    bounds = regular_session_bounds(current.date())
    session_date = (
        current.date() if bounds is not None and current < bounds[0] else next_regular_session(current.date())
    )
    session_bounds = regular_session_bounds(session_date)
    if session_bounds is None:
        return "wait_for_next_published_regular_session"
    return f"wait_until_regular_session:{session_bounds[0].isoformat().lower()}"


__all__ = (
    "FamilySourceSupplyStatus",
    "ResearchAgentSourceSupplyStatus",
    "canonical_source_supply_json",
    "inspect_source_supply",
)

from __future__ import annotations

import hashlib
import json
from typing import assert_never

from trading_agent.day_discovery_ledger import DayDiscoveryCycleState
from trading_agent.day_discovery_ledger_models import (
    DayDiscoveryEventKind,
)
from trading_agent.day_discovery_loop import (
    DayDiscoveryError,
    DayDiscoveryEvidenceView,
    DayDiscoveryLoop,
    ForwardProbeAdmissionRequest,
    _binding,
    _binding_for,
    _canonical_view,
    _capsule_request,
    _preflight_reason,
    _proposal_semantic_hash,
    _safe_reason_token,
    _sha,
)
from trading_agent.day_discovery_state_machine import _event_after, _prepared_from_state
from trading_agent.day_forward_trial_identity import ForwardExecutionLane, market_clock
from trading_agent.day_forward_trial_models import DayForwardTrial
from trading_agent.day_hypothesis_models import HypothesisFamily, HypothesisVersion
from trading_agent.day_research_attempt_binding import preregistered_attempted_artifact_ref
from trading_agent.day_strategy_capsule import build_strategy_capsule
from trading_agent.day_strategy_capsule_models import InvalidStrategyCapsuleError, StrategyCapsule
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json, research_hypothesis_card_key
from trading_agent.generated_strategy_artifact import (
    GeneratedStrategyArtifact,
    GeneratedStrategyArtifactError,
    GeneratedStrategyArtifactPayload,
    GeneratedStrategyArtifactStore,
)
from trading_agent.generated_strategy_execution import GeneratedStrategyExecutionError
from trading_agent.research_identity_models import MarketId
from trading_agent.researcher_agent import (
    ProposedHypothesis,
)
from trading_agent.strategy_research_results import ResearchAttempt
from trading_agent.strategy_research_types import AttemptStatus


def _expected_artifact(
    store: GeneratedStrategyArtifactStore,
    proposal: ProposedHypothesis,
) -> GeneratedStrategyArtifact:
    source = proposal.strategy_draft.source_code
    payload = GeneratedStrategyArtifactPayload(
        source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        hypothesis_id=proposal.card.hypothesis.hypothesis_id,
        card_key=str(research_hypothesis_card_key(proposal.card)),
        research_source_keys=proposal.card.research_source_keys,
        prompt_sha256=proposal.llm_receipt.prompt_sha256,
        response_sha256=proposal.llm_receipt.response_sha256,
        model_id=proposal.llm_receipt.model_id,
        free_parameters=proposal.strategy_draft.free_parameters,
        runtime=store.runtime,
        created_at=proposal.llm_receipt.called_at,
    )
    return GeneratedStrategyArtifact(
        artifact_id=hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest(),
        payload=payload,
    )


def start_artifact_resolution(
    loop: DayDiscoveryLoop,
    view: DayDiscoveryEvidenceView,
    state: DayDiscoveryCycleState,
    branch: int,
) -> None:
    prepared = _prepared_from_state(state)
    if prepared.terminal_reason is not None:
        _finalize_science_and_branch(loop, view, state, prepared.terminal_reason, None)
        return
    artifact = _expected_artifact(
        loop.config.pipeline.stores.strategies,
        prepared.proposal(),
    )
    payload = {
        "prepared_sha256": _sha(canonical_experiment_ledger_json(prepared)),
        "expected_artifact": artifact.model_dump(mode="json"),
        "artifact_ref": preregistered_attempted_artifact_ref(artifact.payload.source_sha256),
    }
    intent = _event_after(
        state.events[-1],
        DayDiscoveryEventKind.RESOLUTION_INTENT,
        branch,
        payload,
        loop.config.clock,
    )
    with loop.config.pipeline.stores.ledger.writer() as writer:
        writer.start_day_discovery_effect(intent)
    if loop.config.fault_injector is not None:
        loop.config.fault_injector("resolution_intent")
    latest = loop.config.pipeline.stores.ledger.reader().day_discovery_cycle_state(state.cycle.cycle_id)
    recover_or_publish_artifact(loop, latest, first_attempt=True)


def recover_or_publish_artifact(
    loop: DayDiscoveryLoop,
    state: DayDiscoveryCycleState,
    *,
    first_attempt: bool,
) -> None:
    intent = state.events[-1]
    payload = json.loads(intent.payload_json)
    expected = GeneratedStrategyArtifact.model_validate_json(
        json.dumps(payload["expected_artifact"], separators=(",", ":"), sort_keys=True)
    )
    kind = DayDiscoveryEventKind.ARTIFACT_OUTCOME_UNKNOWN
    result_payload: dict[str, object] = {"expected_artifact_id": expected.artifact_id}
    if first_attempt:
        prepared = _prepared_from_state(state)
        try:
            published = loop.config.pipeline.stores.strategies.publish(prepared.proposal())
            loaded = loop.config.pipeline.stores.strategies.load(expected.artifact_id)
            if published.artifact != expected or loaded != expected:
                raise GeneratedStrategyArtifactError("artifact_content_invalid")
            kind = DayDiscoveryEventKind.ARTIFACT_VERIFIED
            result_payload = {
                "artifact": loaded.model_dump(mode="json"),
                "artifact_ref": payload["artifact_ref"],
            }
        except GeneratedStrategyArtifactError as error:
            kind = DayDiscoveryEventKind.ARTIFACT_FAILED
            result_payload = {"reason": _safe_reason_token(error.reason) or "artifact_publication_failed"}
    else:
        try:
            loaded = loop.config.pipeline.stores.strategies.load(expected.artifact_id)
            if loaded == expected:
                kind = DayDiscoveryEventKind.ARTIFACT_VERIFIED
                result_payload = {
                    "artifact": loaded.model_dump(mode="json"),
                    "artifact_ref": payload["artifact_ref"],
                }
        except GeneratedStrategyArtifactError:
            pass
    event = _event_after(intent, kind, intent.branch_index, result_payload, loop.config.clock)
    with loop.config.pipeline.stores.ledger.writer() as writer:
        writer.finalize_day_discovery_effect(event)


def start_preflight(
    loop: DayDiscoveryLoop,
    view: DayDiscoveryEvidenceView,
    state: DayDiscoveryCycleState,
) -> None:
    prepared = _prepared_from_state(state)
    artifact_event = state.events[-1]
    artifact_payload = json.loads(artifact_event.payload_json)
    artifact = GeneratedStrategyArtifact.model_validate_json(
        json.dumps(artifact_payload["artifact"], separators=(",", ":"), sort_keys=True)
    )
    binding = _binding_for(
        prepared.attempt_id,
        prepared.version,
        str(artifact_payload["artifact_ref"]),
        prepared.bound_at,
        prepared.search_budget_debit,
        view.search_budget,
    )
    request = _capsule_request(
        prepared.version,
        binding,
        artifact.artifact_id,
        view,
        loop.config.pipeline.stores.strategies,
        loop.config.sandbox,
        prepared.published_at,
    )
    intent_payload = {
        "artifact_id": artifact.artifact_id,
        "request_sha256": _sha(canonical_experiment_ledger_json(prepared) + _canonical_view(view)),
    }
    intent = _event_after(
        artifact_event,
        DayDiscoveryEventKind.PREFLIGHT_INTENT,
        artifact_event.branch_index,
        intent_payload,
        loop.config.clock,
    )
    with loop.config.pipeline.stores.ledger.writer() as writer:
        writer.start_day_discovery_effect(intent)
    if loop.config.fault_injector is not None:
        loop.config.fault_injector("preflight_intent")
    try:
        capsule = build_strategy_capsule(request)
        kind = DayDiscoveryEventKind.PREFLIGHT_VERIFIED
        payload: dict[str, object] = {"capsule": capsule.model_dump(mode="json")}
    except (GeneratedStrategyExecutionError, InvalidStrategyCapsuleError) as error:
        kind = DayDiscoveryEventKind.PREFLIGHT_FAILED
        payload = {"reason": _preflight_reason(error)}
    event = _event_after(intent, kind, intent.branch_index, payload, loop.config.clock)
    with loop.config.pipeline.stores.ledger.writer() as writer:
        writer.finalize_day_discovery_effect(event)


def finalize_authoritative_branch(
    loop: DayDiscoveryLoop,
    view: DayDiscoveryEvidenceView,
    state: DayDiscoveryCycleState,
) -> None:
    last = state.events[-1]
    match last.event_kind:
        case DayDiscoveryEventKind.ARTIFACT_FAILED:
            reason = "artifact_publication_failed"
            capsule = None
        case DayDiscoveryEventKind.ARTIFACT_OUTCOME_UNKNOWN:
            reason = "artifact_outcome_unknown"
            capsule = None
        case DayDiscoveryEventKind.PREFLIGHT_FAILED:
            payload = json.loads(last.payload_json)
            reason = str(payload.get("reason", "sandbox_failed"))
            capsule = None
        case DayDiscoveryEventKind.PREFLIGHT_OUTCOME_UNKNOWN:
            reason = "preflight_outcome_unknown"
            capsule = None
        case DayDiscoveryEventKind.PREFLIGHT_VERIFIED:
            payload = json.loads(last.payload_json)
            capsule = StrategyCapsule.model_validate_json(
                json.dumps(payload["capsule"], separators=(",", ":"), sort_keys=True)
            )
            reason = None
        case unexpected:
            raise DayDiscoveryError(f"day_discovery_terminal_transition_invalid:{unexpected.value}")
    _finalize_science_and_branch(loop, view, state, reason, capsule)


def _finalize_science_and_branch(
    loop: DayDiscoveryLoop,
    view: DayDiscoveryEvidenceView,
    state: DayDiscoveryCycleState,
    reason: str | None,
    capsule: StrategyCapsule | None,
) -> None:
    prepared = _prepared_from_state(state)
    family = prepared.family
    version = prepared.version
    register_science = True
    if reason == "semantic_duplicate":
        family, version = _existing_semantic_parent(loop, prepared.proposal())
        register_science = False
    attempt_branch_index = prepared.branch_index
    if not register_science:
        prior_attempts = loop.config.pipeline.stores.ledger.reader().day_attempts_for_review(
            version.market_id,
            version.hypothesis_version_id,
        )
        attempt_branch_index = 1 + max(
            (stored.attempt.branch_index for stored in prior_attempts),
            default=-1,
        )
    artifact_ref = preregistered_attempted_artifact_ref(version.code_sha256)
    if capsule is not None:
        artifact_ref = capsule.artifact_ref
    status = AttemptStatus.SUCCEEDED if reason is None else AttemptStatus.FAILED
    attempt = ResearchAttempt(
        attempt_id=prepared.attempt_id,
        hypothesis_id=version.hypothesis_version_id,
        branch_index=attempt_branch_index,
        input_hashes=(view.data_manifest_sha256,),
        code_sha256=version.code_sha256,
        data_manifest_sha256=version.data_manifest_sha256,
        started_at=prepared.attempt_started_at,
        finished_at=prepared.attempt_finished_at,
        status=status,
        artifact_refs=(artifact_ref,),
        error_class=reason,
        max_cpu_seconds=view.resource_limits.cpu_seconds,
    )
    binding = _binding(
        attempt,
        version,
        artifact_ref,
        prepared.bound_at,
        prepared.search_budget_debit,
        view.search_budget,
    )
    admission_id = None
    trial = None
    if capsule is not None:
        admission_payload = {
            "admission_id": "",
            "capsule_id": capsule.capsule_id,
            "market_id": view.market_id,
            "registration_completed_bar_at": prepared.published_at,
            "first_eligible_completed_bar_at": view.first_eligible_completed_bar_at,
            "trading_authority": False,
        }
        admission_id = ForwardProbeAdmissionRequest.canonical_id_for(admission_payload)
        trial = _forward_trial(capsule, version, view)
    branch_payload = {
        "accepted": capsule is not None,
        "attempt_id": attempt.attempt_id,
        "family_id": family.family_id,
        "hypothesis_version_id": version.hypothesis_version_id,
        "capsule_id": None if capsule is None else capsule.capsule_id,
        "admission_id": admission_id,
        "trial_id": None if trial is None else trial.trial_id,
        "terminal_reason": reason,
        "search_budget_debit": prepared.search_budget_debit,
    }
    event = _event_after(
        state.events[-1],
        DayDiscoveryEventKind.BRANCH_FINALIZED,
        prepared.branch_index,
        branch_payload,
        loop.config.clock,
    )
    with loop.config.pipeline.stores.ledger.writer() as writer:
        if register_science:
            writer.register_strategy_research(prepared.preregistration)
            writer.register_day_hypothesis_family(family)
            writer.register_day_hypothesis_version(version)
        writer.append_strategy_research_attempt(attempt)
        writer.register_day_research_attempt_binding(binding)
        if capsule is not None:
            writer._register_day_strategy_capsule(capsule)
        if trial is not None:
            writer.register_day_forward_trial(trial)
        writer.finalize_day_discovery_branch(event)


def _forward_trial(
    capsule: StrategyCapsule,
    version: HypothesisVersion,
    view: DayDiscoveryEvidenceView,
) -> DayForwardTrial:
    exchange, timezone = market_clock(view.market_id)
    session_date = view.first_eligible_completed_bar_at.astimezone(timezone).date()
    match view.market_id:
        case MarketId.KR_EQUITIES:
            calendar_refs = tuple(
                item.removeprefix("calendar:")
                for item in view.source_refs
                if item.startswith("calendar:")
            )
            if len(calendar_refs) != 1:
                raise DayDiscoveryError("forward_probe_calendar_unresolved")
            calendar_version = calendar_refs[0]
        case MarketId.US_EQUITIES:
            calendar_version = "us-equity-calendar-v1"
        case unreachable:
            assert_never(unreachable)
    payload = {
        "schema_version": 1,
        "trial_id": "",
        "capsule_id": capsule.capsule_id,
        "hypothesis_version_id": version.hypothesis_version_id,
        "market_id": view.market_id,
        "execution_lane": ForwardExecutionLane.FORWARD_PROBE,
        "session_id": f"{exchange}-{session_date.isoformat()}",
        "session_date": session_date,
        "calendar_snapshot_id": f"calendar://official/{exchange}/{calendar_version}",
        "cost_model_sha256": _sha(canonical_experiment_ledger_json(capsule.cost_model)),
        "source_refs_sha256": _sha(
            json.dumps(version.source_refs, ensure_ascii=True, separators=(",", ":"))
        ),
        "evidence_schema_sha256": _sha(
            json.dumps(capsule.evidence_schema, ensure_ascii=True, separators=(",", ":"))
        ),
        "preregistered_at": capsule.published_at,
        "registration_completed_bar_at": version.registration_completed_bar_at,
        "first_eligible_completed_bar_at": view.first_eligible_completed_bar_at,
        "trading_authority": False,
        "profitability_claim": False,
    }
    return DayForwardTrial.model_validate(
        payload | {"trial_id": DayForwardTrial.canonical_id_for(payload)}
    )


def _existing_semantic_parent(
    loop: DayDiscoveryLoop,
    proposal: ProposedHypothesis,
) -> tuple[HypothesisFamily, HypothesisVersion]:
    reader = loop.config.pipeline.stores.ledger.reader()
    families = {stored.family.family_id: stored.family for stored in reader.day_hypothesis_families()}
    expected = _proposal_semantic_hash(proposal)
    matches = tuple(
        (family, stored.version)
        for stored in reader.day_hypothesis_versions()
        if (family := families.get(stored.version.family_id)) is not None
        and _sha(
            "|".join(
                (
                    family.canonical_question.casefold().strip(),
                    family.economic_mechanism.casefold().strip(),
                    *stored.version.methodology_tags,
                )
            )
        )
        == expected
    )
    if len(matches) != 1:
        raise DayDiscoveryError("semantic_duplicate_parent_invalid")
    return matches[0]

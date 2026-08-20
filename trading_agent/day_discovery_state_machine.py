from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace
from pathlib import Path

from pydantic import ValidationError

from trading_agent.critic_agent import CritiqueReport
from trading_agent.day_discovery_hypothesis_factory import DayHypothesisBuildInput, build_day_hypothesis_contracts
from trading_agent.day_discovery_journal import DayDiscoveryPreparedBranch, PreparedLlmReceipt, PreparedStrategyDraft
from trading_agent.day_discovery_ledger import DayDiscoveryCycleState
from trading_agent.day_discovery_ledger_models import (
    DayDiscoveryBudgetAccount,
    DayDiscoveryBudgetDebit,
    DayDiscoveryCallReservationPayload,
    DayDiscoveryCallResponsePayload,
    DayDiscoveryCycle,
    DayDiscoveryDebitKind,
    DayDiscoveryEvent,
    DayDiscoveryEventKind,
)
from trading_agent.day_discovery_loop import (
    DayDiscoveryCycleReceipt,
    DayDiscoveryCycleResult,
    DayDiscoveryError,
    DayDiscoveryEvidenceView,
    DayDiscoveryLoop,
    _bounded_prompt_view,
    _canonical_view,
    _critique_terminal_reason,
    _cycle_receipt_lease,
    _cycle_time,
    _day_critique,
    _parameter_combination_demand,
    _read_cycle_receipt,
    _require_safe_day_context,
    _sha,
)
from trading_agent.day_strategy_capsule import _replay_input_digest
from trading_agent.experiment_ledger_models import ResearchHypothesisCard, ResearchSource
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerWriterLeaseUnavailableError,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.private_immutable_file import InvalidPrivateImmutableFileError, publish_private_immutable_text
from trading_agent.researcher_agent import (
    CandidateStrategyDraft,
    LlmCallReceipt,
    ProposedHypothesis,
    ResearcherContext,
)
from trading_agent.researcher_llm import (
    ResearcherLlmError,
    ResearcherLlmPlan,
    ResearcherRawCompletion,
    StructuredHypothesisGenerator,
)
from trading_agent.researcher_receipt_store import ResearcherReceiptStoreError


def run_authoritative_cycle(
    loop: DayDiscoveryLoop,
    view: DayDiscoveryEvidenceView,
    context: ResearcherContext,
) -> DayDiscoveryCycleResult:
    from trading_agent.day_discovery_effect_state import (
        finalize_authoritative_branch,
        recover_or_publish_artifact,
        start_artifact_resolution,
        start_preflight,
    )
    if not 1 <= loop.config.max_drafts <= 3:
        raise DayDiscoveryError("max_drafts_out_of_range")
    _require_safe_day_context(context)
    evidence_sha256 = _sha(_canonical_view(view))
    account_seed = {
        "account_id": "",
        "market_id": view.market_id,
        "budget_epoch_ref": view.budget_epoch_ref,
        "debit_limit": view.search_budget,
        "created_at": view.observed_at,
    }
    account_id = DayDiscoveryBudgetAccount.canonical_id_for(account_seed)
    cycle_seed = {
        "cycle_id": "",
        "account_id": account_id,
        "market_id": view.market_id,
        "evidence_sha256": evidence_sha256,
        "cursor_sha256": _sha(view.cursor),
        "opened_at": view.observed_at,
    }
    cycle_id = DayDiscoveryCycle.canonical_id_for(cycle_seed)
    receipt_root = loop.config.cycle_receipt_root or (
        loop.config.pipeline.artifacts.manifest_root / "day-discovery-cycle-receipts"
    )
    receipt_path = receipt_root / f"{cycle_id}.json"
    with _cycle_receipt_lease(receipt_root, cycle_id):
        state = _load_or_open_authoritative_cycle(
            loop,
            view,
            cycle_id,
            evidence_sha256,
            receipt_root,
        )
        if state.events[-1].event_kind is DayDiscoveryEventKind.CYCLE_FINALIZED:
            return _project_final_cycle(
                loop,
                view,
                receipt_path,
                cycle_id,
                evidence_sha256,
                state,
            )
        bounded_context = replace(
            context,
            bounded_day_discovery_json=_bounded_prompt_view(view, state.remaining_budget),
        )
        while state.events[-1].event_kind is not DayDiscoveryEventKind.CYCLE_FINALIZED:
            last = state.events[-1]
            branch = _current_branch(state.events)
            if last.event_kind in {
                DayDiscoveryEventKind.CYCLE_OPENED,
                DayDiscoveryEventKind.BRANCH_FINALIZED,
            }:
                finalized = tuple(
                    event
                    for event in state.events
                    if event.event_kind is DayDiscoveryEventKind.BRANCH_FINALIZED
                )
                accepted = next(
                    (
                        event
                        for event in finalized
                        if json.loads(event.payload_json).get("accepted") is True
                    ),
                    None,
                )
                if accepted is not None or len(finalized) >= loop.config.max_drafts or state.remaining_budget == 0:
                    result = _result_from_branches(view, cycle_id, state.remaining_budget, finalized)
                    final = _event_after(
                        last,
                        DayDiscoveryEventKind.CYCLE_FINALIZED,
                        None,
                        {
                            "ledger_head_event_id": last.event_id,
                            "result": result.model_dump(mode="json"),
                        },
                        loop.config.clock,
                    )
                    with loop.config.pipeline.stores.ledger.writer() as writer:
                        writer.finalize_day_discovery_cycle(final)
                    state = loop.config.pipeline.stores.ledger.reader().day_discovery_cycle_state(cycle_id)
                    continue
                branch = len(finalized)
                created = _reserve_authoritative_call(loop, view, bounded_context, state, branch)
                state = loop.config.pipeline.stores.ledger.reader().day_discovery_cycle_state(cycle_id)
                if not created:
                    continue
                if loop.config.fault_injector is not None:
                    loop.config.fault_injector("call_reserved")
                try:
                    _invoke_and_record_authoritative_call(
                        loop,
                        bounded_context,
                        state.events[-1],
                    )
                except (ResearcherLlmError, ValidationError):
                    terminal = _terminal_branch_event(
                        state.events[-1],
                        "model_call_outcome_unknown",
                        state.remaining_budget,
                        loop.config.clock,
                    )
                    with loop.config.pipeline.stores.ledger.writer() as writer:
                        writer.finalize_day_discovery_branch(terminal)
                state = loop.config.pipeline.stores.ledger.reader().day_discovery_cycle_state(cycle_id)
                continue
            if last.event_kind is DayDiscoveryEventKind.CALL_RESERVED:
                terminal = _terminal_branch_event(
                    last,
                    "model_call_outcome_unknown",
                    state.remaining_budget,
                    loop.config.clock,
                )
                with loop.config.pipeline.stores.ledger.writer() as writer:
                    writer.finalize_day_discovery_branch(terminal)
                state = loop.config.pipeline.stores.ledger.reader().day_discovery_cycle_state(cycle_id)
                continue
            if last.event_kind is DayDiscoveryEventKind.CALL_RESPONSE_RECORDED:
                _prepare_authoritative_branch(loop, view, bounded_context, state, branch)
                state = loop.config.pipeline.stores.ledger.reader().day_discovery_cycle_state(cycle_id)
                continue
            if last.event_kind is DayDiscoveryEventKind.BRANCH_PREPARED:
                start_artifact_resolution(loop, view, state, branch)
                state = loop.config.pipeline.stores.ledger.reader().day_discovery_cycle_state(cycle_id)
                continue
            if last.event_kind is DayDiscoveryEventKind.RESOLUTION_INTENT:
                recover_or_publish_artifact(loop, state, first_attempt=False)
                state = loop.config.pipeline.stores.ledger.reader().day_discovery_cycle_state(cycle_id)
                continue
            if last.event_kind is DayDiscoveryEventKind.ARTIFACT_VERIFIED:
                start_preflight(loop, view, state)
                state = loop.config.pipeline.stores.ledger.reader().day_discovery_cycle_state(cycle_id)
                continue
            if last.event_kind is DayDiscoveryEventKind.PREFLIGHT_INTENT:
                unknown = _event_after(
                    last,
                    DayDiscoveryEventKind.PREFLIGHT_OUTCOME_UNKNOWN,
                    branch,
                    {},
                    loop.config.clock,
                )
                with loop.config.pipeline.stores.ledger.writer() as writer:
                    writer.finalize_day_discovery_effect(unknown)
                state = loop.config.pipeline.stores.ledger.reader().day_discovery_cycle_state(cycle_id)
                continue
            finalize_authoritative_branch(loop, view, state)
            state = loop.config.pipeline.stores.ledger.reader().day_discovery_cycle_state(cycle_id)
        return _project_final_cycle(
            loop,
            view,
            receipt_path,
            cycle_id,
            evidence_sha256,
            state,
        )


def _load_or_open_authoritative_cycle(
    loop: DayDiscoveryLoop,
    view: DayDiscoveryEvidenceView,
    cycle_id: str,
    evidence_sha256: str,
    receipt_root: Path,
):
    ledger = loop.config.pipeline.stores.ledger
    try:
        return ledger.reader().day_discovery_cycle_state(cycle_id)
    except InvalidExperimentLedgerSourceError:
        legacy = receipt_root / f"{cycle_id}.json"
        legacy_parts = tuple(receipt_root.glob(f"{cycle_id}.*.json")) if receipt_root.exists() else ()
        if legacy.exists() or legacy.is_symlink() or legacy_parts:
            raise DayDiscoveryError("legacy_discovery_journal_unmigrated") from None
    opened_at = view.observed_at
    account_payload = {
        "account_id": "",
        "market_id": view.market_id,
        "budget_epoch_ref": view.budget_epoch_ref,
        "debit_limit": view.search_budget,
        "created_at": opened_at,
    }
    account = DayDiscoveryBudgetAccount.model_validate(
        account_payload | {"account_id": DayDiscoveryBudgetAccount.canonical_id_for(account_payload)}
    )
    cycle_payload = {
        "cycle_id": "",
        "account_id": account.account_id,
        "market_id": view.market_id,
        "evidence_sha256": evidence_sha256,
        "cursor_sha256": _sha(view.cursor),
        "opened_at": opened_at,
    }
    cycle = DayDiscoveryCycle.model_validate(
        cycle_payload | {"cycle_id": DayDiscoveryCycle.canonical_id_for(cycle_payload)}
    )
    try:
        with ledger.writer() as writer:
            writer.open_day_discovery_cycle(account, cycle)
    except ExperimentLedgerWriterLeaseUnavailableError:
        raise DayDiscoveryError("day_discovery_writer_lease_unavailable") from None
    except ExperimentLedgerConflictError:
        raise DayDiscoveryError("cycle_evidence_identity_conflict") from None
    return ledger.reader().day_discovery_cycle_state(cycle_id)


def _current_branch(events: tuple[DayDiscoveryEvent, ...]) -> int:
    return sum(event.event_kind is DayDiscoveryEventKind.BRANCH_FINALIZED for event in events)


def _reserve_authoritative_call(loop, view, context, state, branch: int) -> bool:
    generator = loop.config.pipeline.services.generator
    match generator:
        case StructuredHypothesisGenerator():
            plan = generator.plan(context)
        case _:
            prompt = context.bounded_day_discovery_json or "{}"
            prompt_bytes = prompt.encode()
            creator_name = type(generator).__qualname__
            plan = ResearcherLlmPlan(
                prompt=prompt,
                prompt_sha256=hashlib.sha256(prompt_bytes).hexdigest(),
                prompt_bytes_sha256=hashlib.sha256(prompt_bytes).hexdigest(),
                model_id=creator_name,
                seed=None,
                temperature=0.0,
                protocol_sha256=_sha("typed-proposal-v1"),
                creator=creator_name,
                creator_sha256=_sha(creator_name),
                planned_at=_cycle_time(loop.config.clock, state.events[-1].event_at),
            )
    reserved_at = max(plan.planned_at.astimezone(dt.UTC), state.events[-1].event_at)
    creator = f"day-discovery-loop:{os.getpid()}"
    reservation_payload = {
        "reservation_id": "",
        "account_id": state.account.account_id,
        "cycle_id": state.cycle.cycle_id,
        "branch_index": branch,
        "prompt_sha256": plan.prompt_sha256,
        "prompt_bytes_sha256": plan.prompt_bytes_sha256,
        "prompt_length": len(plan.prompt.encode()),
        "model_id": plan.model_id,
        "seed": plan.seed,
        "temperature": plan.temperature,
        "protocol_sha256": plan.protocol_sha256,
        "creator": creator,
        "creator_sha256": _sha(creator),
        "reserved_at": reserved_at,
    }
    reservation = DayDiscoveryCallReservationPayload.model_validate(
        reservation_payload
        | {"reservation_id": DayDiscoveryCallReservationPayload.canonical_id_for(reservation_payload)}
    )
    event = _event_after(
        state.events[-1],
        DayDiscoveryEventKind.CALL_RESERVED,
        branch,
        reservation.model_dump(mode="json"),
        loop.config.clock,
        event_at=reserved_at,
    )
    debit_payload = {
        "debit_id": "",
        "account_id": state.account.account_id,
        "cycle_id": state.cycle.cycle_id,
        "branch_index": branch,
        "debit_kind": DayDiscoveryDebitKind.CALL_RESERVATION,
        "amount": 1,
        "debited_at": reserved_at,
    }
    debit = DayDiscoveryBudgetDebit.model_validate(
        debit_payload | {"debit_id": DayDiscoveryBudgetDebit.canonical_id_for(debit_payload)}
    )
    try:
        with loop.config.pipeline.stores.ledger.writer() as writer:
            created = writer.reserve_day_discovery_call(debit, event)
    except ExperimentLedgerWriterLeaseUnavailableError:
        raise DayDiscoveryError("day_discovery_writer_lease_unavailable") from None
    return created


def _invoke_and_record_authoritative_call(loop, context, reservation_event: DayDiscoveryEvent) -> None:
    reservation = DayDiscoveryCallReservationPayload.model_validate_json(reservation_event.payload_json)
    generator = loop.config.pipeline.services.generator
    match generator:
        case StructuredHypothesisGenerator():
            plan = generator.plan(context)
            if (
                plan.prompt_sha256 != reservation.prompt_sha256
                or len(plan.prompt.encode()) != reservation.prompt_length
            ):
                raise DayDiscoveryError("call_reservation_prompt_mismatch")
            completion = generator.invoke_raw(plan)
            raw = completion.response
            started_at = completion.invocation_started_at
            received_at = completion.received_at
        case _:
            started_at = _cycle_time(loop.config.clock, reservation.reserved_at)
            proposal = generator.propose(context)
            received_at = _cycle_time(loop.config.clock, started_at)
            raw = _recorded_proposal_bytes(proposal)
    response = DayDiscoveryCallResponsePayload(
        reservation_id=reservation.reservation_id,
        response_base64=base64.b64encode(raw).decode("ascii"),
        response_sha256=hashlib.sha256(raw).hexdigest(),
        response_length=len(raw),
        invocation_started_at=started_at,
        received_at=received_at,
    )
    event = _event_after(
        reservation_event,
        DayDiscoveryEventKind.CALL_RESPONSE_RECORDED,
        reservation.branch_index,
        response.model_dump(mode="json"),
        loop.config.clock,
        event_at=received_at,
    )
    with loop.config.pipeline.stores.ledger.writer() as writer:
        writer.record_day_discovery_call_response(event)


def _recorded_proposal_bytes(proposal: ProposedHypothesis) -> bytes:
    return json.dumps(
        {
            "card": proposal.card.model_dump(mode="json"),
            "cited_sources": [source.model_dump(mode="json") for source in proposal.cited_sources],
            "llm_receipt": asdict(proposal.llm_receipt),
            "strategy_draft": asdict(proposal.strategy_draft),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=lambda value: value.isoformat() if isinstance(value, dt.datetime) else value,
    ).encode()


def _proposal_from_recorded_bytes(raw: bytes) -> ProposedHypothesis:
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise DayDiscoveryError("model_response_malformed")
        receipt_payload = payload["llm_receipt"]
        if not isinstance(receipt_payload, dict):
            raise DayDiscoveryError("model_response_malformed")
        receipt_payload["called_at"] = dt.datetime.fromisoformat(str(receipt_payload["called_at"]))
        draft_payload = payload["strategy_draft"]
        if not isinstance(draft_payload, dict):
            raise DayDiscoveryError("model_response_malformed")
        sources_payload = payload["cited_sources"]
        if not isinstance(sources_payload, list):
            raise DayDiscoveryError("model_response_malformed")
        return ProposedHypothesis(
            card=ResearchHypothesisCard.model_validate(payload["card"]),
            cited_sources=tuple(ResearchSource.model_validate(item) for item in sources_payload),
            llm_receipt=LlmCallReceipt(**receipt_payload),
            strategy_draft=CandidateStrategyDraft(
                source_code=str(draft_payload["source_code"]),
                free_parameters=tuple(str(value) for value in draft_payload["free_parameters"]),
                methodology_tags=tuple(str(value) for value in draft_payload["methodology_tags"]),
            ),
        )
    except (KeyError, TypeError, ValueError, ValidationError):
        raise DayDiscoveryError("model_response_malformed") from None


def _prepare_authoritative_branch(
    loop: DayDiscoveryLoop,
    view: DayDiscoveryEvidenceView,
    context: ResearcherContext,
    state: DayDiscoveryCycleState,
    branch: int,
) -> None:
    response_event = state.events[-1]
    response = DayDiscoveryCallResponsePayload.model_validate_json(response_event.payload_json)
    raw = base64.b64decode(response.response_base64, validate=True)
    generator = loop.config.pipeline.services.generator
    try:
        match generator:
            case StructuredHypothesisGenerator():
                plan = generator.plan(context)
                reservation_event = state.events[-2]
                reservation = DayDiscoveryCallReservationPayload.model_validate_json(
                    reservation_event.payload_json
                )
                plan = replace(plan, planned_at=reservation.reserved_at)
                if (
                    plan.prompt_sha256 != reservation.prompt_sha256
                    or plan.model_id != reservation.model_id
                    or plan.protocol_sha256 != reservation.protocol_sha256
                ):
                    raise DayDiscoveryError("call_reservation_plan_mismatch")
                completion = ResearcherRawCompletion(
                    response=raw,
                    response_sha256=response.response_sha256,
                    response_length=response.response_length,
                    invocation_started_at=response.invocation_started_at,
                    received_at=response.received_at,
                )
                proposal = generator.parse_raw(plan, completion, context)
            case _:
                proposal = _proposal_from_recorded_bytes(raw)
        base = loop.config.pipeline.services.critic.critique(
            proposal,
            loop.config.pipeline.stores.ledger,
        )
        available_before_call = state.remaining_budget + 1
        supplemental = _day_critique(proposal, view, available_before_call)
        critique = CritiqueReport(base.objections + supplemental.objections)
        loop.config.pipeline.stores.receipts.record_critique(proposal, critique)
        reason = _critique_terminal_reason(critique)
    except (
        DayDiscoveryError,
        ResearcherLlmError,
        ResearcherReceiptStoreError,
        ValidationError,
        ValueError,
    ):
        terminal = _terminal_branch_event(
            response_event,
            "model_response_malformed",
            state.remaining_budget,
            loop.config.clock,
        )
        with loop.config.pipeline.stores.ledger.writer() as writer:
            writer.finalize_day_discovery_branch(terminal)
        return
    attempt_started_at = proposal.llm_receipt.called_at.astimezone(dt.UTC)
    actual_registration_at = max(attempt_started_at, view.completed_bar_at, view.observed_at)
    if attempt_started_at < view.observed_at:
        reason = "proposal_time_invalid"
    if actual_registration_at >= view.first_eligible_completed_bar_at:
        reason = "forward_probe_not_future_only"
    contract_first_eligible_at = max(
        view.first_eligible_completed_bar_at,
        actual_registration_at + dt.timedelta(microseconds=1),
    )
    attempt_finished_at = _cycle_time(loop.config.clock, actual_registration_at)
    bound_at = _cycle_time(loop.config.clock, attempt_finished_at)
    published_at = _cycle_time(loop.config.clock, bound_at)
    family, version, preregistration = build_day_hypothesis_contracts(
        proposal,
        DayHypothesisBuildInput(
            market_id=view.market_id,
            observed_at=actual_registration_at,
            completed_bar_at=view.completed_bar_at,
            first_eligible_completed_bar_at=contract_first_eligible_at,
            universe_snapshot_id=view.universe_snapshot_id,
            universe_snapshot_at=view.universe_snapshot_at,
            source_refs=view.source_refs,
            data_manifest_sha256=view.data_manifest_sha256,
            search_budget=state.remaining_budget + 1,
        ),
        terminal=reason is not None,
    )
    demand = _parameter_combination_demand(proposal)
    available_before_call = state.remaining_budget + 1
    planned_demand = 1 if reason == "budget_exhausted" else demand
    debit = planned_demand
    prepared = DayDiscoveryPreparedBranch(
        cycle_id=state.cycle.cycle_id,
        evidence_sha256=state.cycle.evidence_sha256,
        branch_index=branch,
        market_id=view.market_id.value,
        search_budget=view.search_budget,
        remaining_budget_before=available_before_call,
        proposal_card=proposal.card.model_dump(mode="json"),
        cited_sources=proposal.cited_sources,
        llm_receipt=PreparedLlmReceipt(**asdict(proposal.llm_receipt)),
        strategy_draft=PreparedStrategyDraft(**asdict(proposal.strategy_draft)),
        terminal_reason=reason,
        family=family,
        version=version,
        preregistration=preregistration,
        attempt_id=_sha(f"{state.cycle.cycle_id}:{branch}:{version.hypothesis_version_id}"),
        attempt_started_at=attempt_started_at,
        attempt_finished_at=attempt_finished_at,
        bound_at=bound_at,
        published_at=published_at,
        search_budget_debit=debit,
    )
    event_at = max(response_event.event_at, published_at)
    event = _event_after(
        response_event,
        DayDiscoveryEventKind.BRANCH_PREPARED,
        branch,
        {"cartesian_demand": planned_demand, "prepared": prepared.model_dump(mode="json")},
        loop.config.clock,
        event_at=event_at,
    )
    top_up = None
    if debit > 1:
        debit_payload = {
            "debit_id": "",
            "account_id": state.account.account_id,
            "cycle_id": state.cycle.cycle_id,
            "branch_index": branch,
            "debit_kind": DayDiscoveryDebitKind.CARTESIAN_TOP_UP,
            "amount": debit - 1,
            "debited_at": event_at,
        }
        top_up = DayDiscoveryBudgetDebit.model_validate(
            debit_payload | {"debit_id": DayDiscoveryBudgetDebit.canonical_id_for(debit_payload)}
        )
    with loop.config.pipeline.stores.ledger.writer() as writer:
        writer.prepare_day_discovery_branch(top_up, event)


def _prepared_from_state(state: DayDiscoveryCycleState) -> DayDiscoveryPreparedBranch:
    event = next(
        event
        for event in reversed(state.events)
        if event.event_kind is DayDiscoveryEventKind.BRANCH_PREPARED
    )
    payload = json.loads(event.payload_json)
    prepared = DayDiscoveryPreparedBranch.model_validate_json(
        json.dumps(payload["prepared"], separators=(",", ":"), sort_keys=True)
    )
    response_event = next(
        candidate
        for candidate in reversed(state.events)
        if candidate.event_kind is DayDiscoveryEventKind.CALL_RESPONSE_RECORDED
        and candidate.branch_index == event.branch_index
    )
    reservation_event = next(
        candidate
        for candidate in reversed(state.events)
        if candidate.event_kind is DayDiscoveryEventKind.CALL_RESERVED
        and candidate.branch_index == event.branch_index
    )
    response = DayDiscoveryCallResponsePayload.model_validate_json(response_event.payload_json)
    reservation = DayDiscoveryCallReservationPayload.model_validate_json(
        reservation_event.payload_json
    )
    if (
        prepared.cycle_id != state.cycle.cycle_id
        or prepared.evidence_sha256 != state.cycle.evidence_sha256
        or prepared.market_id != state.cycle.market_id.value
        or prepared.search_budget_debit
        != sum(debit.amount for debit in state.debits if debit.branch_index == prepared.branch_index)
    ):
        raise DayDiscoveryError("prepared_branch_authority_mismatch")
    raw = base64.b64decode(response.response_base64, validate=True)
    if reservation.protocol_sha256 == _sha("typed-proposal-v1"):
        if _proposal_from_recorded_bytes(raw) != prepared.proposal():
            raise DayDiscoveryError("prepared_branch_response_mismatch")
    elif (
        prepared.llm_receipt.response_sha256 != response.response_sha256
        or prepared.llm_receipt.prompt_sha256 != reservation.prompt_sha256
        or prepared.llm_receipt.model_id != reservation.model_id
    ):
        raise DayDiscoveryError("prepared_branch_response_mismatch")
    return prepared


def _terminal_branch_event(
    previous: DayDiscoveryEvent,
    reason: str,
    remaining_budget: int,
    clock: Callable[[], dt.datetime] | None,
) -> DayDiscoveryEvent:
    branch = previous.branch_index or 0
    return _event_after(
        previous,
        DayDiscoveryEventKind.BRANCH_FINALIZED,
        branch,
        {
            "accepted": False,
            "attempt_id": _sha(f"{previous.cycle_id}:{branch}:{reason}"),
            "family_id": None,
            "hypothesis_version_id": None,
            "capsule_id": None,
            "admission_id": None,
            "terminal_reason": reason,
            "search_budget_debit": 1,
            "remaining_budget": remaining_budget,
        },
        clock,
    )


def _result_from_branches(
    view: DayDiscoveryEvidenceView,
    cycle_id: str,
    remaining_budget: int,
    branches: tuple[DayDiscoveryEvent, ...],
) -> DayDiscoveryCycleResult:
    payloads = tuple(json.loads(event.payload_json) for event in branches)
    accepted = next((payload for payload in payloads if payload["accepted"] is True), None)
    latest = accepted or (payloads[-1] if payloads else None)
    terminal_reason = "budget_exhausted" if latest is None else latest["terminal_reason"]
    return DayDiscoveryCycleResult(
        cycle_id=cycle_id,
        attempt_ids=tuple(str(payload["attempt_id"]) for payload in payloads),
        family_id=None if latest is None else latest["family_id"],
        hypothesis_version_id=None if latest is None else latest["hypothesis_version_id"],
        capsule_id=None if latest is None else latest["capsule_id"],
        admission_id=None if latest is None else latest["admission_id"],
        accepted=accepted is not None,
        terminal_reason=None if accepted is not None else str(terminal_reason),
        drafts_attempted=len(payloads),
        remaining_budget=remaining_budget,
        first_eligible_completed_bar_at=view.first_eligible_completed_bar_at,
    )


def _event_after(
    previous: DayDiscoveryEvent,
    kind: DayDiscoveryEventKind,
    branch_index: int | None,
    payload: Mapping[str, object],
    clock: Callable[[], dt.datetime] | None,
    *,
    event_at: dt.datetime | None = None,
) -> DayDiscoveryEvent:
    timestamp = event_at or _cycle_time(clock, previous.event_at)
    event_payload = {
        "event_id": "",
        "cycle_id": previous.cycle_id,
        "sequence": previous.sequence + 1,
        "previous_event_id": previous.event_id,
        "branch_index": branch_index,
        "event_kind": kind,
        "event_at": max(timestamp, previous.event_at),
        "payload_json": json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            default=lambda value: value.isoformat() if isinstance(value, dt.datetime) else value,
        ),
    }
    return DayDiscoveryEvent.model_validate(
        event_payload | {"event_id": DayDiscoveryEvent.canonical_id_for(event_payload)}
    )


def _cycle_local_remaining_budget(state: DayDiscoveryCycleState) -> int:
    last_branch = next(
        (
            event
            for event in reversed(state.events)
            if event.event_kind is DayDiscoveryEventKind.BRANCH_FINALIZED
        ),
        None,
    )
    if last_branch is None:
        if state.debits:
            raise DayDiscoveryError("cycle_final_budget_invalid")
        return 0
    payload = json.loads(last_branch.payload_json)
    if "remaining_budget" in payload:
        remaining = payload["remaining_budget"]
    else:
        prepared_event = next(
            (
                event
                for event in reversed(state.events)
                if event.event_kind is DayDiscoveryEventKind.BRANCH_PREPARED
                and event.branch_index == last_branch.branch_index
            ),
            None,
        )
        if prepared_event is None:
            raise DayDiscoveryError("cycle_final_budget_invalid")
        prepared = json.loads(prepared_event.payload_json)["prepared"]
        remaining = prepared["remaining_budget_before"] - sum(
            debit.amount for debit in state.debits if debit.branch_index == last_branch.branch_index
        )
    if not isinstance(remaining, int) or remaining < 0:
        raise DayDiscoveryError("cycle_final_budget_invalid")
    return remaining


def _project_final_cycle(
    loop: DayDiscoveryLoop,
    view: DayDiscoveryEvidenceView,
    path: Path,
    cycle_id: str,
    evidence_sha256: str,
    state: DayDiscoveryCycleState,
) -> DayDiscoveryCycleResult:
    final_event = state.events[-1]
    final_payload = json.loads(final_event.payload_json)
    if final_payload.get("ledger_head_event_id") != final_event.previous_event_id:
        raise DayDiscoveryError("cycle_final_ledger_head_invalid")
    result = DayDiscoveryCycleResult.model_validate(final_payload.get("result"))
    if result.remaining_budget != _cycle_local_remaining_budget(state):
        raise DayDiscoveryError("cycle_final_budget_invalid")
    if result.accepted:
        if result.capsule_id is None or result.hypothesis_version_id is None:
            raise DayDiscoveryError("cycle_final_acceptance_invalid")
        reader = loop.config.pipeline.stores.ledger.reader()
        stored_capsule = reader.day_strategy_capsule(result.capsule_id)
        stored_version = reader.day_hypothesis_version(result.hypothesis_version_id)
        if stored_capsule is None or stored_version is None:
            raise DayDiscoveryError("cycle_final_science_join_invalid")
        capsule = stored_capsule.capsule
        artifact_id = capsule.generated_artifact_id
        preflight = capsule.preflight_receipt
        if (
            artifact_id is None
            or preflight is None
            or capsule.market_id is not view.market_id
            or capsule.evidence_schema != view.evidence_schema
            or capsule.resource_limits.to_generated_limits() != view.resource_limits
            or capsule.hypothesis_version_id != result.hypothesis_version_id
            or preflight.replay_input_sha256 != _replay_input_digest(view.replay_bars)
        ):
            raise DayDiscoveryError("cycle_final_capsule_binding_invalid")
        artifact = loop.config.pipeline.stores.strategies.load(artifact_id)
        if artifact.payload.source_sha256 != capsule.artifact_sha256:
            raise DayDiscoveryError("cycle_final_artifact_binding_invalid")
    receipt = DayDiscoveryCycleReceipt(
        cycle_id=cycle_id,
        evidence_sha256=evidence_sha256,
        ledger_head_event_id=final_event.event_id,
        result=result,
    )
    canonical = json.dumps(
        receipt.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    if path.exists() or path.is_symlink():
        replay = _read_cycle_receipt(
            path,
            cycle_id,
            evidence_sha256,
            final_event.event_id,
        )
        if replay != result:
            raise DayDiscoveryError("cycle_receipt_authority_mismatch")
        return result
    try:
        publish_private_immutable_text(path, canonical)
    except InvalidPrivateImmutableFileError:
        raise DayDiscoveryError("cycle_receipt_publication_failed") from None
    return result

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Final, override

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import (
    hypothesis_registration_key,
    strategy_version_registration_key,
)
from trading_agent.experiment_ledger_models import (
    ExperimentTrialEvent,
    ExperimentTrialRegistration,
    HypothesisRegistration,
    StrategyLifecycleEvent,
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
    StrategyVersionRegistration,
    TrialEventKind,
    TrialKind,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerStore,
    InvalidExperimentLedgerSourceError,
    StoredExperimentTrialRegistration,
)
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.research_identity_models import AgentFamily, MarketId
from trading_agent.signal_contract_models import (
    SignalActionability,
    SignalEntryType,
    TradeSignalEnvelope,
)
from trading_agent.swing_new_high_rvol import NewHighRvolConfig
from trading_agent.swing_research_contract import SWING_RESEARCH_CONTRACT
from trading_agent.swing_shadow_store import (
    ShadowEventKind,
    SwingShadowEvent,
    SwingShadowReader,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

_TERMINAL_KINDS: Final = frozenset(
    {
        ShadowEventKind.EXPIRED,
        ShadowEventKind.STOPPED,
        ShadowEventKind.TARGETED,
        ShadowEventKind.TIME_EXIT,
    }
)
_EVALUATOR_VERSION: Final = "swing_shadow_terminal_v1"
_FEED_ENTITLEMENT: Final = "internal_shadow_daily_ohlcv"
_EVIDENCE_BUDGET: Final = (
    "signal:1",
    "signal_created:1",
    "terminal_event:1",
)
_LIFECYCLE_POLICY_VERSION: Final = "strategy_lifecycle_v1"


class InvalidSwingShadowTrialSourceError(ValueError):
    @override
    def __str__(self) -> str:
        return "US swing shadow trial의 전향적 원장 증거를 확인하지 못했습니다"


@dataclass(frozen=True, slots=True)
class SwingTrialRegistrationResult:
    created: bool
    registration: ExperimentTrialRegistration


@dataclass(frozen=True, slots=True)
class SwingTrialEventResult:
    created: bool
    event: ExperimentTrialEvent


def swing_shadow_trial_id(signal: TradeSignalEnvelope) -> str:
    _require_canonical_signal_shape(signal)
    digest = hashlib.sha256(
        f"{signal.signal_id}|{signal.producer_strategy_version}".encode()
    ).hexdigest()[:16]
    return f"swing-shadow-{signal.observed_at.astimezone(NEW_YORK):%Y%m%d}-{digest}"


def swing_shadow_trial_data_version(
    signal: TradeSignalEnvelope,
    created: SwingShadowEvent,
) -> str:
    _require_canonical_signal_shape(signal)
    _require_created_evidence(signal, created)
    return _sha256_payload(
        {
            "signal": signal.model_dump(mode="json"),
            "signal_created": created.model_dump(mode="json"),
        }
    )


def swing_shadow_trial_artifact_sha256s(
    signal: TradeSignalEnvelope,
    events: tuple[SwingShadowEvent, ...],
) -> tuple[str, ...]:
    _require_canonical_signal_shape(signal)
    _require_terminal_evidence(signal, events, planned_end=None)
    artifacts = (_sha256_model(signal), *(_sha256_model(event) for event in events))
    return tuple(sorted(artifacts))


def register_swing_shadow_trial(
    *,
    experiment_ledger: ExperimentLedgerStore,
    shadow_ledger: SwingShadowReader,
    signal_id: str,
    runtime_code_version: str,
    registered_at: dt.datetime,
) -> SwingTrialRegistrationResult:
    try:
        _require_aware(registered_at)
        signal, created = _verified_signal_created(shadow_ledger, signal_id)
        planned_start = _planned_start(signal)
        planned_end = _planned_end(planned_start)
        open_at, _ = _bounds(planned_start)
        data_version = swing_shadow_trial_data_version(signal, created)
        trial_id = swing_shadow_trial_id(signal)
        hypothesis = _verified_hypothesis_card(experiment_ledger)
        existing = _trial_by_signal(experiment_ledger, trial_id, data_version)
        if existing is not None:
            if registered_at < existing.registration.registered_at:
                raise InvalidSwingShadowTrialSourceError
            version = _verified_version(
                experiment_ledger,
                runtime_code_version,
                hypothesis,
            )
            _verified_lifecycle(experiment_ledger, version, created, planned_start, hypothesis)
            expected = _trial_registration(
                signal,
                data_version=data_version,
                registered_at=existing.registration.registered_at,
                planned_start=planned_start,
                planned_end=planned_end,
            )
            if existing.registration != expected:
                raise InvalidSwingShadowTrialSourceError
            return SwingTrialRegistrationResult(False, existing.registration)
        if registered_at < created.observed_at or registered_at >= open_at:
            raise InvalidSwingShadowTrialSourceError
        # The source-session decision must follow version evidence, never a later transport request.
        version = _expected_version(runtime_code_version, created.observed_at, hypothesis)
        lifecycle = _lifecycle_registration(version, created, planned_start, hypothesis)
        registration = _trial_registration(
            signal,
            data_version=data_version,
            registered_at=registered_at,
            planned_start=planned_start,
            planned_end=planned_end,
        )
    except InvalidSwingShadowTrialSourceError:
        raise
    except _SOURCE_ERRORS:
        raise InvalidSwingShadowTrialSourceError from None

    try:
        with experiment_ledger.writer() as writer:
            _ = writer.register_strategy_version(version)
            _ = writer.append_lifecycle_event(lifecycle)
            created_trial = writer.register_trial(registration)
    except (ExperimentLedgerConflictError, InvalidExperimentLedgerSourceError, sqlite3.Error, ValueError) as error:
        raise InvalidSwingShadowTrialSourceError from error
    return SwingTrialRegistrationResult(created_trial, registration)


def start_swing_shadow_trial(
    *,
    experiment_ledger: ExperimentLedgerStore,
    shadow_ledger: SwingShadowReader,
    signal_id: str,
    started_at: dt.datetime,
) -> SwingTrialEventResult:
    try:
        signal, created = _verified_signal_created(shadow_ledger, signal_id)
        registration = _verified_registered_trial(experiment_ledger, signal, created)
        open_at, close_at = _bounds(registration.planned_start)
        if not _aware(started_at) or not open_at <= started_at < close_at:
            raise InvalidSwingShadowTrialSourceError
        events = experiment_ledger.trial_events(registration.trial_id)
        if events:
            if len(events) != 1 or events[0].event.event_kind is not TrialEventKind.STARTED:
                raise InvalidSwingShadowTrialSourceError
            return SwingTrialEventResult(False, events[0].event)
        event = ExperimentTrialEvent(
            trial_id=registration.trial_id,
            sequence=1,
            event_kind=TrialEventKind.STARTED,
            occurred_at=started_at,
            artifact_sha256s=(),
            reason_codes=(),
            previous_event_key=None,
        )
    except InvalidSwingShadowTrialSourceError:
        raise
    except _SOURCE_ERRORS:
        raise InvalidSwingShadowTrialSourceError from None

    try:
        with experiment_ledger.writer() as writer:
            created_event = writer.append_trial_event(event)
    except (ExperimentLedgerConflictError, InvalidExperimentLedgerSourceError, sqlite3.Error, ValueError) as error:
        raise InvalidSwingShadowTrialSourceError from error
    return SwingTrialEventResult(created_event, event)


def finalize_swing_shadow_trial(
    *,
    experiment_ledger: ExperimentLedgerStore,
    shadow_ledger: SwingShadowReader,
    signal_id: str,
    finalized_at: dt.datetime,
) -> SwingTrialEventResult:
    try:
        _require_aware(finalized_at)
        signal, created = _verified_signal_created(shadow_ledger, signal_id)
        registration = _verified_registered_trial(experiment_ledger, signal, created)
        shadow_events = shadow_ledger.events(signal_id)
        terminal = _require_terminal_evidence(signal, shadow_events, planned_end=registration.planned_end)
        if finalized_at < terminal.observed_at:
            raise InvalidSwingShadowTrialSourceError
        events = experiment_ledger.trial_events(registration.trial_id)
        started = next(iter(events), None)
        if started is None or len(events) > 2 or started.event.event_kind is not TrialEventKind.STARTED:
            raise InvalidSwingShadowTrialSourceError
        artifacts = swing_shadow_trial_artifact_sha256s(signal, shadow_events)
        existing = events[1] if len(events) == 2 else None
        event = ExperimentTrialEvent(
            trial_id=registration.trial_id,
            sequence=2,
            event_kind=TrialEventKind.COMPLETED,
            occurred_at=finalized_at if existing is None else existing.event.occurred_at,
            artifact_sha256s=artifacts,
            reason_codes=(),
            previous_event_key=started.event_key,
        )
        if existing is not None:
            if existing.event != event:
                raise InvalidSwingShadowTrialSourceError
            return SwingTrialEventResult(False, existing.event)
    except InvalidSwingShadowTrialSourceError:
        raise
    except _SOURCE_ERRORS:
        raise InvalidSwingShadowTrialSourceError from None

    try:
        with experiment_ledger.writer() as writer:
            created_event = writer.append_trial_event(event)
    except (ExperimentLedgerConflictError, InvalidExperimentLedgerSourceError, sqlite3.Error, ValueError) as error:
        raise InvalidSwingShadowTrialSourceError from error
    return SwingTrialEventResult(created_event, event)


def _verified_signal_created(
    shadow_ledger: SwingShadowReader,
    signal_id: str,
) -> tuple[TradeSignalEnvelope, SwingShadowEvent]:
    signals = tuple(signal for signal in shadow_ledger.signals() if signal.signal_id == signal_id)
    if len(signals) != 1:
        raise InvalidSwingShadowTrialSourceError
    signal = signals[0]
    _require_canonical_signal_shape(signal)
    events = shadow_ledger.events(signal_id)
    if not events or events[0].kind is not ShadowEventKind.SIGNAL_CREATED:
        raise InvalidSwingShadowTrialSourceError
    created = events[0]
    _require_created_evidence(signal, created)
    return signal, created


def _verified_hypothesis_card(experiment_ledger: ExperimentLedgerStore) -> HypothesisRegistration:
    hypotheses = tuple(
        stored
        for stored in experiment_ledger.hypotheses()
        if stored.registration.hypothesis_id == SWING_RESEARCH_CONTRACT.hypothesis_id
    )
    cards = tuple(
        stored
        for stored in experiment_ledger.research_hypothesis_cards()
        if stored.card.hypothesis.hypothesis_id == SWING_RESEARCH_CONTRACT.hypothesis_id
    )
    if len(hypotheses) != 1 or len(cards) != 1:
        raise InvalidSwingShadowTrialSourceError
    hypothesis = hypotheses[0].registration
    if (
        hypothesis.experiment_scope != SWING_RESEARCH_CONTRACT.experiment_scope
        or hypothesis.experiment_scope_key != experiment_scope_key(SWING_RESEARCH_CONTRACT.experiment_scope)
        or hypothesis.primary_lane is not SWING_RESEARCH_CONTRACT.experiment_scope.primary_lane
        or hypothesis.hypothesis != SWING_RESEARCH_CONTRACT.hypothesis
        or hypothesis.falsification_rule != SWING_RESEARCH_CONTRACT.falsification_rule
        or hypothesis.source_registered_at != SWING_RESEARCH_CONTRACT.experiment_scope.registered_at
        or cards[0].card.hypothesis != hypothesis
    ):
        raise InvalidSwingShadowTrialSourceError
    return hypothesis


def _expected_version(
    runtime_code_version: str,
    recorded_at: dt.datetime,
    hypothesis: HypothesisRegistration,
) -> StrategyVersionRegistration:
    return StrategyVersionRegistration(
        strategy_id=SWING_RESEARCH_CONTRACT.strategy_id,
        strategy_version=SWING_RESEARCH_CONTRACT.strategy_version,
        hypothesis_id=hypothesis.hypothesis_id,
        experiment_scope_key=hypothesis.experiment_scope_key,
        lane_id=hypothesis.primary_lane,
        code_version=runtime_code_version,
        parameter_set=SWING_RESEARCH_CONTRACT.parameter_set,
        data_contract=SWING_RESEARCH_CONTRACT.data_contract,
        cost_model=SWING_RESEARCH_CONTRACT.cost_model,
        portfolio_policy=SWING_RESEARCH_CONTRACT.portfolio_policy,
        source_registered_at=hypothesis.source_registered_at,
        ledger_recorded_at=recorded_at,
    )


def _verified_version(
    experiment_ledger: ExperimentLedgerStore,
    runtime_code_version: str,
    hypothesis: HypothesisRegistration,
) -> StrategyVersionRegistration:
    versions = tuple(
        stored
        for stored in experiment_ledger.strategy_versions()
        if stored.registration.strategy_version == SWING_RESEARCH_CONTRACT.strategy_version
    )
    if len(versions) != 1:
        raise InvalidSwingShadowTrialSourceError
    expected = _expected_version(
        runtime_code_version,
        versions[0].registration.ledger_recorded_at,
        hypothesis,
    )
    if versions[0].registration != expected:
        raise InvalidSwingShadowTrialSourceError
    return expected


def _lifecycle_registration(
    version: StrategyVersionRegistration,
    created: SwingShadowEvent,
    planned_start: dt.date,
    hypothesis: HypothesisRegistration,
) -> StrategyLifecycleEvent:
    return StrategyLifecycleEvent(
        strategy_version=version.strategy_version,
        sequence=1,
        event_kind=StrategyLifecycleEventKind.REGISTRATION,
        from_state=None,
        to_state=StrategyLifecycleState.EXPERIMENTAL_SHADOW,
        policy_version=_LIFECYCLE_POLICY_VERSION,
        decision_session_date=created.session_date,
        effective_session_date=planned_start,
        decided_at=created.observed_at,
        evidence_keys=tuple(
            sorted(
                (
                    str(experiment_scope_key(SWING_RESEARCH_CONTRACT.experiment_scope)),
                    str(hypothesis_registration_key(hypothesis)),
                    str(strategy_version_registration_key(version)),
                )
            )
        ),
        reason_codes=("existing_contract_import",),
        previous_event_key=None,
    )


def _verified_lifecycle(
    experiment_ledger: ExperimentLedgerStore,
    version: StrategyVersionRegistration,
    created: SwingShadowEvent,
    planned_start: dt.date,
    hypothesis: HypothesisRegistration,
) -> None:
    events = experiment_ledger.lifecycle_events(version.strategy_version)
    expected = _lifecycle_registration(version, created, planned_start, hypothesis)
    if len(events) != 1 or events[0].event != expected:
        raise InvalidSwingShadowTrialSourceError


def _trial_registration(
    signal: TradeSignalEnvelope,
    *,
    data_version: str,
    registered_at: dt.datetime,
    planned_start: dt.date,
    planned_end: dt.date,
) -> ExperimentTrialRegistration:
    return ExperimentTrialRegistration(
        trial_id=swing_shadow_trial_id(signal),
        strategy_version=SWING_RESEARCH_CONTRACT.strategy_version,
        trial_kind=TrialKind.SHADOW_FORWARD,
        experiment_scope=SWING_RESEARCH_CONTRACT.experiment_scope,
        experiment_scope_key=experiment_scope_key(SWING_RESEARCH_CONTRACT.experiment_scope),
        evaluator_version=_EVALUATOR_VERSION,
        data_version=data_version,
        feed_entitlement=_FEED_ENTITLEMENT,
        planned_start=planned_start,
        planned_end=planned_end,
        registered_at=registered_at,
        evidence_budget=_EVIDENCE_BUDGET,
    )


def _trial_by_signal(
    experiment_ledger: ExperimentLedgerStore,
    trial_id: str,
    data_version: str,
) -> StoredExperimentTrialRegistration | None:
    matching_data = tuple(
        stored
        for stored in experiment_ledger.trials()
        if stored.registration.strategy_version == SWING_RESEARCH_CONTRACT.strategy_version
        and stored.registration.data_version == data_version
    )
    matching_id = tuple(stored for stored in experiment_ledger.trials() if stored.registration.trial_id == trial_id)
    if len(matching_data) > 1 or len(matching_id) > 1:
        raise InvalidSwingShadowTrialSourceError
    candidate = matching_id[0] if matching_id else None
    if matching_data and (candidate is None or matching_data[0].registration.trial_id != trial_id):
        raise InvalidSwingShadowTrialSourceError
    return candidate


def _verified_registered_trial(
    experiment_ledger: ExperimentLedgerStore,
    signal: TradeSignalEnvelope,
    created: SwingShadowEvent,
) -> ExperimentTrialRegistration:
    hypothesis = _verified_hypothesis_card(experiment_ledger)
    data_version = swing_shadow_trial_data_version(signal, created)
    stored = _trial_by_signal(experiment_ledger, swing_shadow_trial_id(signal), data_version)
    if stored is None:
        raise InvalidSwingShadowTrialSourceError
    registration = stored.registration
    version = _verified_version(
        experiment_ledger,
        runtime_code_version=_stored_runtime_code_version(experiment_ledger),
        hypothesis=hypothesis,
    )
    _verified_lifecycle(experiment_ledger, version, created, registration.planned_start, hypothesis)
    expected = _trial_registration(
        signal,
        data_version=data_version,
        registered_at=registration.registered_at,
        planned_start=_planned_start(signal),
        planned_end=_planned_end(_planned_start(signal)),
    )
    if registration != expected:
        raise InvalidSwingShadowTrialSourceError
    return registration


def _stored_runtime_code_version(experiment_ledger: ExperimentLedgerStore) -> str:
    versions = tuple(
        stored
        for stored in experiment_ledger.strategy_versions()
        if stored.registration.strategy_version == SWING_RESEARCH_CONTRACT.strategy_version
    )
    if len(versions) != 1:
        raise InvalidSwingShadowTrialSourceError
    return versions[0].registration.code_version


def _require_canonical_signal_shape(signal: TradeSignalEnvelope) -> None:
    evidence_ids = tuple(evidence.canonical_id for evidence in signal.evidence_refs)
    if (
        signal.strategy_lane.market_id is not MarketId.US_EQUITIES
        or signal.strategy_lane.agent_family is not AgentFamily.SWING_TRADING
        or signal.strategy_lane.strategy_id != SWING_RESEARCH_CONTRACT.strategy_id
        or signal.producer_strategy_version != SWING_RESEARCH_CONTRACT.strategy_version
        or signal.entry_type is not SignalEntryType.STOP_TRIGGER
        or signal.actionability is not SignalActionability.CONDITIONAL
        or not _aware(signal.observed_at)
        or signal.valid_until != _next_regular_close(signal.observed_at.astimezone(NEW_YORK).date())
        or len(evidence_ids) != 1
    ):
        raise InvalidSwingShadowTrialSourceError


def _require_created_evidence(signal: TradeSignalEnvelope, created: SwingShadowEvent) -> None:
    evidence_id = f"swing_shadow/daily_source:{created.source_key}"
    if (
        created.signal_id != signal.signal_id
        or created.kind is not ShadowEventKind.SIGNAL_CREATED
        or created.observed_at != signal.observed_at
        or created.session_date != signal.observed_at.astimezone(NEW_YORK).date()
        or tuple(evidence.canonical_id for evidence in signal.evidence_refs) != (evidence_id,)
    ):
        raise InvalidSwingShadowTrialSourceError


def _require_terminal_evidence(
    signal: TradeSignalEnvelope,
    events: tuple[SwingShadowEvent, ...],
    *,
    planned_end: dt.date | None,
) -> SwingShadowEvent:
    if not events:
        raise InvalidSwingShadowTrialSourceError
    _require_created_evidence(signal, events[0])
    terminal = events[-1]
    if terminal.kind not in _TERMINAL_KINDS:
        raise InvalidSwingShadowTrialSourceError
    event_kinds = tuple(event.kind for event in events)
    expected_kinds = (
        (ShadowEventKind.SIGNAL_CREATED, ShadowEventKind.EXPIRED)
        if terminal.kind is ShadowEventKind.EXPIRED
        else (
            ShadowEventKind.SIGNAL_CREATED,
            ShadowEventKind.ENTRY_FILLED,
            terminal.kind,
        )
    )
    if (
        event_kinds != expected_kinds
        or any(event.signal_id != signal.signal_id for event in events)
        or any(event.observed_at < events[index - 1].observed_at for index, event in enumerate(events) if index)
        or any(event.session_date < events[index - 1].session_date for index, event in enumerate(events) if index)
        or any(event.kind in _TERMINAL_KINDS for event in events[:-1])
        or (planned_end is not None and terminal.session_date > planned_end)
        or terminal.session_date < _planned_start(signal)
    ):
        raise InvalidSwingShadowTrialSourceError
    return terminal


def _planned_start(signal: TradeSignalEnvelope) -> dt.date:
    return signal.valid_until.astimezone(NEW_YORK).date()


def _planned_end(planned_start: dt.date) -> dt.date:
    remaining = NewHighRvolConfig().max_holding_sessions
    candidate = planned_start
    for _ in range(90):
        candidate += dt.timedelta(days=1)
        if regular_session_bounds(candidate) is not None:
            remaining -= 1
            if remaining == 0:
                return candidate
    raise InvalidSwingShadowTrialSourceError


def _next_regular_close(session_date: dt.date) -> dt.datetime:
    candidate = session_date + dt.timedelta(days=1)
    for _ in range(14):
        bounds = regular_session_bounds(candidate)
        if bounds is not None:
            return bounds[1]
        candidate += dt.timedelta(days=1)
    raise InvalidSwingShadowTrialSourceError


def _bounds(session_date: dt.date) -> tuple[dt.datetime, dt.datetime]:
    bounds = regular_session_bounds(session_date)
    if bounds is None:
        raise InvalidSwingShadowTrialSourceError
    return bounds


def _require_aware(value: dt.datetime) -> None:
    if not _aware(value):
        raise InvalidSwingShadowTrialSourceError


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _sha256_model(model: TradeSignalEnvelope | SwingShadowEvent) -> str:
    return _sha256_payload(model.model_dump(mode="json"))


def _sha256_payload(payload: object) -> str:
    material = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


_SOURCE_ERRORS: Final = (
    ExperimentLedgerConflictError,
    InvalidExperimentLedgerSourceError,
    OSError,
    sqlite3.Error,
    TypeError,
    ValidationError,
    ValueError,
)


__all__ = (
    "InvalidSwingShadowTrialSourceError",
    "SwingTrialEventResult",
    "SwingTrialRegistrationResult",
    "finalize_swing_shadow_trial",
    "register_swing_shadow_trial",
    "start_swing_shadow_trial",
    "swing_shadow_trial_artifact_sha256s",
    "swing_shadow_trial_data_version",
    "swing_shadow_trial_id",
)

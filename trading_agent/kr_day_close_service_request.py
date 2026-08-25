from __future__ import annotations

import datetime as dt
from collections import defaultdict
from pathlib import Path
from typing import Final, override
from zoneinfo import ZoneInfo

from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.day_learning_report_models import (
    DayDecisionDiagnostic,
    DayDecisionOutcome,
    DayDecisionStage,
)
from trading_agent.day_strategy_capsule_models import StrategyCapsule
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kis_kr_session_calendar_models import KrSessionCalendarSnapshot, KrSessionDay
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_day_capsule_outcomes import (
    KrDayCapsuleOutcome,
    KrDayCapsuleOutcomeAttempt,
    KrDayCapsuleTerminalKind,
    project_kr_day_capsule_outcome,
)
from trading_agent.kr_day_capsule_shadow_models import KrDayCapsuleShadowEvent
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.kr_day_close_service_config import KrDayCloseServiceConfig
from trading_agent.kr_day_decision_models import KrDayDecisionEvent, KrDayDecisionStatus
from trading_agent.kr_day_decision_store import KrDayDecisionStore
from trading_agent.kr_day_market_close_report import KrDayMarketCloseRequest
from trading_agent.research_identity_models import MarketId

_KST: Final = ZoneInfo("Asia/Seoul")
_CLOSE: Final = dt.time(15, 30)
_SIGNAL_PREFIX: Final = "kr-day-decision-"


class InvalidKrDayCloseRequestSourceError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day-close request source is invalid"


class KrDayCloseNotReadyError(ValueError):
    __slots__ = ("reason", "session_date")

    def __init__(self, reason: str, session_date: dt.date) -> None:
        self.reason = reason
        self.session_date = session_date
        super().__init__(reason)


def build_kr_day_close_request(
    config: KrDayCloseServiceConfig,
    observed_at: dt.datetime,
) -> KrDayMarketCloseRequest:
    local = observed_at.astimezone(_KST)
    snapshot, day = _latest_calendar(config.calendar_store, local.date(), local)
    if not (day.business_day and day.trading_day and day.open_day):
        raise KrDayCloseNotReadyError("session_not_open", local.date())
    close_at = dt.datetime.combine(local.date(), _CLOSE, tzinfo=_KST)
    if local < close_at:
        raise KrDayCloseNotReadyError("pre_close", local.date())
    ledger = ExperimentLedgerStore(config.experiment_ledger)
    if not ledger.is_initialized():
        raise InvalidKrDayCloseRequestSourceError
    trials = tuple(
        state.trial
        for state in ledger.day_forward_trials(MarketId.KR_EQUITIES)
        if state.trial.session_date == local.date()
    )
    if not trials or len(trials) > 3 or len({trial.capsule_id for trial in trials}) != len(trials):
        raise InvalidKrDayCloseRequestSourceError
    capsule_by_id = {
        stored.capsule.capsule_id: stored.capsule
        for stored in ledger.day_strategy_capsules(MarketId.KR_EQUITIES)
    }
    if any(
        trial.capsule_id not in capsule_by_id
        or capsule_by_id[trial.capsule_id].hypothesis_version_id != trial.hypothesis_version_id
        or trial.calendar_snapshot_id != f"calendar://official/XKRX/{snapshot.snapshot_id}"
        for trial in trials
    ):
        raise InvalidKrDayCloseRequestSourceError
    selected_capsules = {
        trial.capsule_id: capsule_by_id[trial.capsule_id]
        for trial in trials
    }
    shadows = _session_shadows(
        config.shadow_store,
        local.date(),
        tuple(selected_capsules),
        snapshot.snapshot_id,
    )
    decisions = _session_decisions(config.decision_store, local.date(), tuple(selected_capsules))
    _require_decision_shadow_authority(decisions, shadows, selected_capsules)
    outcomes = tuple(
        _project_outcome(
            capsule_by_id[trial.capsule_id].attempt_binding_id,
            trial.trial_id,
            trial.capsule_id,
            trial.hypothesis_version_id,
            local.date(),
            tuple(event for event in shadows if event.capsule_id == trial.capsule_id),
            tuple(event for event in decisions if event.capsule_id == trial.capsule_id),
        )
        for trial in sorted(trials, key=lambda item: item.capsule_id)
    )
    active, queued = _existing_authority(ledger, local.date(), tuple(sorted(selected_capsules)))
    champion = DayAgentVersionStore(config.state_root / "day-agent-versions.sqlite3").reader().champion()
    if champion is not None and set(champion.playbook_ids) != set(selected_capsules):
        raise InvalidKrDayCloseRequestSourceError
    return KrDayMarketCloseRequest(
        session_date=local.date(),
        official_close_at=close_at,
        finalized_at=close_at,
        calendar_snapshot=snapshot,
        expected_capsule_ids=tuple(sorted(trial.capsule_id for trial in trials)),
        shadow_events=shadows,
        decision_event_ids=tuple(sorted(event.event_id for event in decisions)),
        outcomes=outcomes,
        active_capsule_ids=active,
        queued_capsule_ids=queued,
        risk_incident_ids=(),
        data_incident_ids=(),
        agent_version_id=None if champion is None else champion.version_id,
        diagnostics=() if champion is None else _close_diagnostics(decisions, outcomes),
    )


def _close_diagnostics(
    decisions: tuple[KrDayDecisionEvent, ...],
    outcomes: tuple[KrDayCapsuleOutcome, ...],
) -> tuple[DayDecisionDiagnostic, ...]:
    decision_ids = tuple(sorted(event.event_id for event in decisions))
    outcome_ids = tuple(sorted(outcome.outcome_id for outcome in outcomes))
    exit_refuted = any(
        outcome.net_return is not None and outcome.net_return <= 0
        for outcome in outcomes
    )
    failed = any(outcome.kind.value in {"blocked", "failed", "censored"} for outcome in outcomes)
    failure = DayDecisionStage.EXIT if exit_refuted else DayDecisionStage.EXECUTION_QUALITY if failed else None
    return tuple(
        DayDecisionDiagnostic(
            stage=stage,
            outcome=(
                DayDecisionOutcome.REFUTED
                if stage is failure
                else DayDecisionOutcome.SUPPORTED
            ),
            score=0.1 if stage is failure else 0.8,
            evidence_ids=(
                outcome_ids
                if stage is DayDecisionStage.EXIT
                else decision_ids
            ),
            reason_codes=("kr_close_refuted",) if stage is failure else ("kr_close_supported",),
        )
        for stage in DayDecisionStage
    )


def _latest_calendar(
    path: Path,
    session_date: dt.date,
    observed_at: dt.datetime,
) -> tuple[KrSessionCalendarSnapshot, KrSessionDay]:
    if not path.is_file():
        raise InvalidKrDayCloseRequestSourceError
    snapshots = KisKrSessionCalendarStore(path).snapshots()
    candidates = tuple(
        (snapshot, day)
        for snapshot in snapshots
        for day in snapshot.payload.days
        if day.session_date == session_date and snapshot.payload.observed_at <= observed_at
    )
    if not candidates:
        raise InvalidKrDayCloseRequestSourceError
    return max(candidates, key=lambda item: item[0].payload.observed_at)


def _session_shadows(
    path: Path,
    session_date: dt.date,
    known_capsules: tuple[str, ...],
    calendar_snapshot_id: str,
) -> tuple[KrDayCapsuleShadowEvent, ...]:
    if not path.exists():
        return ()
    if not path.is_file():
        raise InvalidKrDayCloseRequestSourceError
    events = tuple(event for event in KrDayCapsuleShadowStore(path).events() if event.session_date == session_date)
    if any(
        event.capsule_id not in known_capsules
        or event.calendar_snapshot_id != calendar_snapshot_id
        for event in events
    ):
        raise InvalidKrDayCloseRequestSourceError
    return events


def _session_decisions(
    path: Path,
    session_date: dt.date,
    known_capsules: tuple[str, ...],
) -> tuple[KrDayDecisionEvent, ...]:
    if not path.is_file():
        raise InvalidKrDayCloseRequestSourceError
    events = tuple(event for event in KrDayDecisionStore(path).events() if event.session_date == session_date)
    if not events or any(event.capsule_id not in known_capsules for event in events):
        raise InvalidKrDayCloseRequestSourceError
    return events


def _require_decision_shadow_authority(
    decisions: tuple[KrDayDecisionEvent, ...],
    shadows: tuple[KrDayCapsuleShadowEvent, ...],
    capsules: dict[str, StrategyCapsule],
) -> None:
    decisions_by_capsule: dict[str, list[KrDayDecisionEvent]] = defaultdict(list)
    for decision in decisions:
        decisions_by_capsule[decision.capsule_id].append(decision)
    shadow_capsules = {event.capsule_id for event in shadows}
    if not shadow_capsules <= set(capsules) or set(decisions_by_capsule) != set(capsules):
        raise InvalidKrDayCloseRequestSourceError
    for capsule_id, events in decisions_by_capsule.items():
        hypothesis = capsules[capsule_id].hypothesis_version_id
        if any(event.hypothesis_version_id != hypothesis for event in events) or (
            capsule_id not in shadow_capsules
            and any(
                event.status
                not in {
                    KrDayDecisionStatus.REJECTED,
                    KrDayDecisionStatus.BLOCKED,
                    KrDayDecisionStatus.EXPIRED,
                }
                for event in events
            )
        ):
            raise InvalidKrDayCloseRequestSourceError
    decision_ids = {event.event_id for event in decisions}
    for event in shadows:
        signal = event.signal_id
        if (
            signal is not None
            and signal.startswith(_SIGNAL_PREFIX)
            and signal.removeprefix(_SIGNAL_PREFIX) not in decision_ids
        ):
            raise InvalidKrDayCloseRequestSourceError


def _project_outcome(
    attempt_id: str,
    trial_id: str,
    capsule_id: str,
    hypothesis_version_id: str,
    session_date: dt.date,
    shadows: tuple[KrDayCapsuleShadowEvent, ...],
    decisions: tuple[KrDayDecisionEvent, ...],
) -> KrDayCapsuleOutcome:
    if shadows:
        return project_kr_day_capsule_outcome(
            KrDayCapsuleOutcomeAttempt(
                attempt_id=attempt_id,
                capsule_id=capsule_id,
                hypothesis_version_id=hypothesis_version_id,
                trial_id=trial_id,
                session_date=session_date,
                events=shadows,
            )
        )
    terminal = max(decisions, key=lambda event: (event.observed_at, event.event_id))
    kind = (
        KrDayCapsuleTerminalKind.BLOCKED
        if terminal.status is KrDayDecisionStatus.BLOCKED
        else KrDayCapsuleTerminalKind.NO_SIGNAL
    )
    reason_codes = ",".join(reason.value.lower() for reason in terminal.reason_codes)
    return KrDayCapsuleOutcome.seal(
        {
            "attempt_id": attempt_id,
            "capsule_id": capsule_id,
            "hypothesis_version_id": hypothesis_version_id,
            "trial_id": trial_id,
            "session_date": session_date,
            "kind": kind,
            "reason": f"decision_{terminal.status.value.lower()}:{reason_codes}",
            "terminal_event_id": terminal.event_id,
            "net_return": None,
            "realized_r": None,
        }
    )


def _existing_authority(
    ledger: ExperimentLedgerStore,
    session_date: dt.date,
    fallback: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    policies = tuple(
        policy
        for policy in ledger.day_exploration_policies(MarketId.KR_EQUITIES)
        if policy.payload.effective_session_date == session_date
    )
    if not policies:
        return fallback, ()
    policy = policies[-1].payload
    return policy.active_capsule_ids, policy.queued_capsule_ids


__all__ = (
    "InvalidKrDayCloseRequestSourceError",
    "KrDayCloseNotReadyError",
    "build_kr_day_close_request",
)

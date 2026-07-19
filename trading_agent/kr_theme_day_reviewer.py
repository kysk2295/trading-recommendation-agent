from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Final, Protocol, Self, override
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, InvalidExperimentLedgerSourceError
from trading_agent.kr_theme_day_review_models import (
    CURRENT_KR_THEME_DAY_REVIEWER_VERSION,
    KrThemeDayReviewCounts,
    KrThemeDayReviewEvent,
    decide_kr_theme_day_review,
    kr_theme_day_review_metrics,
)
from trading_agent.kr_theme_day_review_store import (
    InvalidKrThemeDayReviewStoreError,
    KrThemeDayReviewStore,
)
from trading_agent.kr_theme_day_shadow_entry_models import KrThemeDayShadowEntry
from trading_agent.kr_theme_day_shadow_entry_store import (
    InvalidKrThemeDayShadowEntryStoreError,
    KrThemeDayShadowEntryStore,
)
from trading_agent.kr_theme_day_shadow_exit_models import KrThemeDayShadowExit
from trading_agent.kr_theme_day_shadow_exit_store import (
    InvalidKrThemeDayShadowExitStoreError,
    KrThemeDayShadowExitStore,
)
from trading_agent.kr_theme_day_trial import InvalidKrThemeDayTrialError, require_exact_kr_theme_day_trial
from trading_agent.kr_theme_day_trial_terminal_models import (
    InvalidKrThemeDayTrialTerminalModelError,
    KrThemeDayTrialTerminalArtifact,
)
from trading_agent.kr_theme_day_trial_terminal_store import (
    InvalidKrThemeDayTrialTerminalStoreError,
    KrThemeDayTrialTerminalStore,
)
from trading_agent.multi_market_trial_models import MultiMarketExperimentTrialRegistration

_KST: Final = ZoneInfo("Asia/Seoul")
_SESSION_CLOSE: Final = dt.time(15, 30)


class InvalidKrThemeDayReviewError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day Reviewer could not verify exact terminal evidence"


class KrThemeDayReviewRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_version: str
    as_of_session: dt.date
    reviewed_at: dt.datetime

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        local = self.reviewed_at.astimezone(_KST) if _aware(self.reviewed_at) else None
        if (
            not self.strategy_version
            or self.strategy_version != self.strategy_version.strip()
            or local is None
            or local.date() < self.as_of_session
            or (local.date() == self.as_of_session and local.time() < _SESSION_CLOSE)
        ):
            raise InvalidKrThemeDayReviewError
        return self


@dataclass(frozen=True, slots=True)
class KrThemeDayReviewSources:
    experiment_ledger: ExperimentLedgerReader
    entry_store: KrThemeDayShadowEntryStore
    exit_store: KrThemeDayShadowExitStore
    terminal_store: KrThemeDayTrialTerminalStore
    review_store: KrThemeDayReviewStore


@dataclass(frozen=True, slots=True)
class KrThemeDayReviewResult:
    created: bool
    event: KrThemeDayReviewEvent


class _TrialBound(Protocol):
    trial_id: str


def review_kr_theme_day_strategy(
    sources: KrThemeDayReviewSources,
    request: KrThemeDayReviewRequest,
) -> KrThemeDayReviewResult:
    try:
        request = KrThemeDayReviewRequest.model_validate(request.model_dump(mode="python"))
        event = _build_event(sources, request)
        created = sources.review_store.append(event)
    except (
        AttributeError,
        InvalidExperimentLedgerSourceError,
        InvalidKrThemeDayReviewError,
        InvalidKrThemeDayReviewStoreError,
        InvalidKrThemeDayShadowEntryStoreError,
        InvalidKrThemeDayShadowExitStoreError,
        InvalidKrThemeDayTrialError,
        InvalidKrThemeDayTrialTerminalModelError,
        InvalidKrThemeDayTrialTerminalStoreError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrThemeDayReviewError from None
    return KrThemeDayReviewResult(created, event)


def _build_event(
    sources: KrThemeDayReviewSources,
    request: KrThemeDayReviewRequest,
) -> KrThemeDayReviewEvent:
    trials = tuple(
        sorted(
            (
                stored.registration
                for stored in sources.experiment_ledger.multi_market_trials()
                if stored.registration.strategy_version == request.strategy_version
                and stored.registration.planned_start <= request.as_of_session
            ),
            key=lambda trial: (trial.planned_start, trial.trial_id),
        )
    )
    if not trials:
        raise InvalidKrThemeDayReviewError
    for trial in trials:
        require_exact_kr_theme_day_trial(sources.experiment_ledger, trial)
    artifacts = _artifacts(sources.terminal_store, request, trials)
    entries = sources.entry_store.entries()
    exits = sources.exit_store.exits()
    terminal_event_keys: list[str] = []
    completed_exits: list[KrThemeDayShadowExit] = []
    for trial, artifact in zip(trials, artifacts, strict=True):
        terminal_event_keys.append(_verify_terminal(sources.experiment_ledger, trial, artifact, request))
        trial_entries = tuple(sorted(_for_trial(entries, trial.trial_id), key=lambda item: item.entry_id))
        trial_exits = tuple(sorted(_for_trial(exits, trial.trial_id), key=lambda item: item.exit_id))
        _require_artifact_sources(artifact, trial_entries, trial_exits)
        if artifact.payload.terminal_kind is TrialEventKind.COMPLETED:
            completed_exits.extend(trial_exits)
    completed_exits.sort(key=lambda exit: (exit.exit_at, exit.entry_id, exit.exit_id))
    returns = tuple(exit.net_return for exit in completed_exits)
    realized_rs = tuple(exit.realized_r for exit in completed_exits)
    compounded, mean_r, win_rate, max_drawdown = kr_theme_day_review_metrics(returns, realized_rs)
    kinds = tuple(artifact.payload.terminal_kind for artifact in artifacts)
    counts = KrThemeDayReviewCounts(
        completed_sessions=kinds.count(TrialEventKind.COMPLETED),
        censored_sessions=kinds.count(TrialEventKind.CENSORED),
        failed_sessions=kinds.count(TrialEventKind.FAILED),
        completed_trades=len(completed_exits),
    )
    decision = decide_kr_theme_day_review(counts)
    existing = sources.review_store.review_event(
        request.strategy_version,
        request.as_of_session.isoformat(),
        CURRENT_KR_THEME_DAY_REVIEWER_VERSION,
    )
    reviewed_at = request.reviewed_at if existing is None else existing.reviewed_at
    return KrThemeDayReviewEvent(
        strategy_version=request.strategy_version,
        as_of_session=request.as_of_session,
        reviewer_version=CURRENT_KR_THEME_DAY_REVIEWER_VERSION,
        trial_ids=tuple(trial.trial_id for trial in trials),
        terminal_event_keys=tuple(terminal_event_keys),
        terminal_artifact_sha256s=tuple(artifact.artifact_id for artifact in artifacts),
        terminal_kinds=kinds,
        completed_sessions=counts.completed_sessions,
        censored_sessions=counts.censored_sessions,
        failed_sessions=counts.failed_sessions,
        completed_trades=counts.completed_trades,
        trade_exit_ids=tuple(exit.exit_id for exit in completed_exits),
        trade_net_returns=returns,
        trade_realized_rs=realized_rs,
        compounded_return=compounded,
        mean_realized_r=mean_r,
        win_rate=win_rate,
        max_drawdown=max_drawdown,
        action=decision.action,
        reasons=decision.reasons,
        blockers=decision.blockers,
        reviewed_at=reviewed_at,
        automatic_state_change_allowed=False,
        order_authority_change_allowed=False,
        allocation_change_allowed=False,
    )


def _artifacts(
    store: KrThemeDayTrialTerminalStore,
    request: KrThemeDayReviewRequest,
    trials: tuple[MultiMarketExperimentTrialRegistration, ...],
) -> tuple[KrThemeDayTrialTerminalArtifact, ...]:
    expected_ids = tuple(trial.trial_id for trial in trials)
    artifacts = tuple(
        sorted(
            (
                artifact
                for artifact in store.artifacts()
                if artifact.payload.strategy_version == request.strategy_version
                and artifact.payload.session_date <= request.as_of_session
            ),
            key=lambda artifact: (artifact.payload.session_date, artifact.payload.trial_id),
        )
    )
    if tuple(artifact.payload.trial_id for artifact in artifacts) != expected_ids:
        raise InvalidKrThemeDayReviewError
    return artifacts


def _verify_terminal(
    ledger: ExperimentLedgerReader,
    trial: MultiMarketExperimentTrialRegistration,
    artifact: KrThemeDayTrialTerminalArtifact,
    request: KrThemeDayReviewRequest,
) -> str:
    events = ledger.multi_market_trial_events(trial.trial_id)
    if (
        len(events) != 2
        or events[0].event.event_kind is not TrialEventKind.STARTED
        or events[1].event.event_kind is not artifact.payload.terminal_kind
        or events[1].event.artifact_sha256s != (artifact.artifact_id,)
        or events[1].event.reason_codes != artifact.payload.reason_codes
        or str(events[0].event_key) != artifact.payload.started_event_key
        or events[1].event.previous_event_key != events[0].event_key
        or events[1].event.occurred_at != artifact.payload.terminal_at
        or events[1].event.occurred_at > request.reviewed_at
    ):
        raise InvalidKrThemeDayReviewError
    return str(events[1].event_key)


def _require_artifact_sources(
    artifact: KrThemeDayTrialTerminalArtifact,
    entries: tuple[KrThemeDayShadowEntry, ...],
    exits: tuple[KrThemeDayShadowExit, ...],
) -> None:
    if (
        artifact.payload.entry_ids != tuple(entry.entry_id for entry in entries)
        or artifact.payload.entry_payload_sha256s != tuple(_payload_sha(entry) for entry in entries)
        or artifact.payload.exit_ids != tuple(exit.exit_id for exit in exits)
        or artifact.payload.exit_payload_sha256s != tuple(_payload_sha(exit) for exit in exits)
    ):
        raise InvalidKrThemeDayReviewError


def _for_trial[T: _TrialBound](items: tuple[T, ...], trial_id: str) -> tuple[T, ...]:
    return tuple(item for item in items if item.trial_id == trial_id)


def _payload_sha(value: KrThemeDayShadowEntry | KrThemeDayShadowExit) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(value).encode()).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None

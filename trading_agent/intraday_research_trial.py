from __future__ import annotations

import datetime as dt
import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from trading_agent.challenger_replay_runner import run_intraday_walk_forward
from trading_agent.daily_research_contract import strategy_contract, strategy_version_identity
from trading_agent.experiment_ledger_keys import experiment_trial_event_key
from trading_agent.experiment_ledger_models import (
    ExperimentTrialEvent,
    ExperimentTrialRegistration,
    TrialEventKind,
    TrialKind,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.intraday_research_artifacts import (
    IntradayExperimentArtifact,
    IntradayExperimentPayload,
    intraday_experiment_artifact,
    load_intraday_experiment_artifact,
    publish_intraday_experiment_artifact,
)
from trading_agent.intraday_research_loop_models import (
    IntradayResearchManifest,
    IntradayWalkForwardError,
    IntradayWalkForwardRequest,
    IntradayWalkForwardResult,
)
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.models import BarInput
from trading_agent.strategy_factory import StrategyMode
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds


@dataclass(frozen=True, slots=True)
class IntradayTrialExecutionContext:
    manifest: IntradayResearchManifest
    experiment_ledger: Path
    artifact_root: Path
    data_version: str
    manifest_sha256: str
    bars: tuple[BarInput, ...]


@dataclass(frozen=True, slots=True)
class IntradayHistoricalTrialError(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return f"intraday historical trial failed: {self.reason}"


def run_or_replay_intraday_trial(
    context: IntradayTrialExecutionContext,
    strategy: StrategyMode,
) -> tuple[IntradayExperimentArtifact, bool]:
    registration = _trial_registration(context, strategy)
    started = ExperimentTrialEvent(
        trial_id=registration.trial_id,
        sequence=1,
        event_kind=TrialEventKind.STARTED,
        occurred_at=context.manifest.registered_at + dt.timedelta(seconds=2),
        artifact_sha256s=(),
        reason_codes=(),
        previous_event_key=None,
    )
    ledger = ExperimentLedgerStore(context.experiment_ledger)
    with ledger.writer() as writer:
        _ = writer.register_trial(registration)
        _ = writer.append_trial_event(started)
    events = ExperimentLedgerReader(ledger.path).trial_events(registration.trial_id)
    if len(events) == 2:
        terminal = events[-1].event
        if terminal.event_kind is not TrialEventKind.COMPLETED or len(terminal.artifact_sha256s) != 1:
            raise IntradayHistoricalTrialError("terminal_is_not_completed")
        identity = terminal.artifact_sha256s[0]
        return load_intraday_experiment_artifact(
            context.artifact_root / f"intraday_walk_forward_{identity}.json"
        ), False
    if len(events) != 1:
        raise IntradayHistoricalTrialError("invalid_trial_event_chain")
    try:
        result = _run_walk_forward(context, strategy)
    except IntradayWalkForwardError:
        failed = ExperimentTrialEvent(
            trial_id=registration.trial_id,
            sequence=2,
            event_kind=TrialEventKind.FAILED,
            occurred_at=context.manifest.registered_at + dt.timedelta(seconds=3),
            artifact_sha256s=(),
            reason_codes=("bounded_historical_experiment_failed",),
            previous_event_key=str(experiment_trial_event_key(started)),
        )
        with ExperimentLedgerStore(context.experiment_ledger).writer() as writer:
            _ = writer.append_trial_event(failed)
        raise IntradayHistoricalTrialError("bounded_walk_forward_failed") from None
    artifact = intraday_experiment_artifact(
        IntradayExperimentPayload(
            trial_id=registration.trial_id,
            strategy_version=registration.strategy_version,
            evaluator_version=registration.evaluator_version,
            data_version=context.data_version,
            manifest_sha256=context.manifest_sha256,
            registered_at=registration.registered_at,
            started_at=started.occurred_at,
            completed_at=context.manifest.registered_at + dt.timedelta(seconds=3),
            result=result,
        )
    )
    _, created = publish_intraday_experiment_artifact(context.artifact_root, artifact)
    completed = ExperimentTrialEvent(
        trial_id=registration.trial_id,
        sequence=2,
        event_kind=TrialEventKind.COMPLETED,
        occurred_at=artifact.payload.completed_at,
        artifact_sha256s=(artifact.artifact_id,),
        reason_codes=(),
        previous_event_key=str(experiment_trial_event_key(started)),
    )
    with ledger.writer() as writer:
        _ = writer.append_trial_event(completed)
    return artifact, created


def _run_walk_forward(
    context: IntradayTrialExecutionContext,
    strategy: StrategyMode,
) -> IntradayWalkForwardResult:
    with tempfile.TemporaryDirectory(prefix=f"m6-{strategy.value}-") as temporary:
        return run_intraday_walk_forward(
            IntradayWalkForwardRequest(
                bars=context.bars,
                strategy=strategy,
                minimum_training_sessions=context.manifest.minimum_training_sessions,
                per_side_cost_bps=context.manifest.per_side_total_cost_bps,
                bootstrap_samples=context.manifest.bootstrap_samples,
                rss_limit_gib=context.manifest.rss_limit_gib,
            ),
            Path(temporary),
        )


def _trial_registration(
    context: IntradayTrialExecutionContext,
    strategy: StrategyMode,
) -> ExperimentTrialRegistration:
    contract = strategy_contract(strategy)
    version = strategy_version_identity(strategy, context.manifest.code_version)
    seed = f"{version}:{context.data_version}:{context.manifest_sha256}"
    trial_id = f"m6-{strategy.value}-{hashlib.sha256(seed.encode()).hexdigest()[:16]}"
    registered_at = context.manifest.registered_at + dt.timedelta(seconds=1)
    planned = _next_regular_session(registered_at)
    return ExperimentTrialRegistration(
        trial_id=trial_id,
        strategy_version=version,
        trial_kind=TrialKind.HISTORICAL_REPLAY,
        experiment_scope=contract.experiment_scope,
        experiment_scope_key=str(experiment_scope_key(contract.experiment_scope)),
        evaluator_version=context.manifest.evaluator_version,
        data_version=context.data_version,
        feed_entitlement="bounded local point-in-time intraday CSV; no provider or broker access",
        planned_start=planned,
        planned_end=planned,
        registered_at=registered_at,
        evidence_budget=tuple(
            sorted(
                (
                    f"max_bars:{context.manifest.max_bars}",
                    f"max_sessions:{context.manifest.max_sessions}",
                    f"rss_limit_gib:{context.manifest.rss_limit_gib}",
                )
            )
        ),
    )


def _next_regular_session(recorded_at: dt.datetime) -> dt.date:
    decision_date = recorded_at.astimezone(NEW_YORK).date()
    for offset in range(1, 11):
        candidate = decision_date + dt.timedelta(days=offset)
        if regular_session_bounds(candidate) is not None:
            return candidate
    raise IntradayHistoricalTrialError("regular_session_not_found")


__all__ = (
    "IntradayHistoricalTrialError",
    "IntradayTrialExecutionContext",
    "run_or_replay_intraday_trial",
)

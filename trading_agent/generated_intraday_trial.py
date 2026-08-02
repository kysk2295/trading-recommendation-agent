from __future__ import annotations

import datetime as dt
import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from trading_agent.experiment_ledger_keys import experiment_trial_event_key
from trading_agent.experiment_ledger_models import (
    ExperimentTrialEvent,
    ExperimentTrialRegistration,
    TrialEventKind,
    TrialKind,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.generated_intraday_evaluator import (
    GeneratedIntradayEvaluationError,
    GeneratedIntradayEvaluationRequest,
    run_generated_intraday_walk_forward,
)
from trading_agent.generated_intraday_research_models import (
    GeneratedIntradayResearchManifest,
    GeneratedStrategySelection,
)
from trading_agent.generated_strategy_artifact import PublishedGeneratedStrategy
from trading_agent.generated_strategy_execution import GeneratedStrategyExecutionError
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.intraday_research_artifacts import (
    IntradayExperimentArtifact,
    IntradayExperimentPayload,
    intraday_experiment_artifact,
    load_intraday_experiment_artifact,
    publish_intraday_experiment_artifact,
)
from trading_agent.models import BarInput
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds


class GeneratedIntradayTrialError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class GeneratedIntradayTrialContext:
    manifest: GeneratedIntradayResearchManifest
    experiment_ledger: Path
    artifact_root: Path
    data_version: str
    manifest_sha256: str
    bars: tuple[BarInput, ...]
    published: PublishedGeneratedStrategy
    sandbox: GeneratedStrategySandbox


def run_or_replay_generated_intraday_trial(
    context: GeneratedIntradayTrialContext,
    selection: GeneratedStrategySelection,
) -> tuple[IntradayExperimentArtifact, bool]:
    registration = _registration(context, selection)
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
            raise GeneratedIntradayTrialError("terminal_is_not_completed")
        return (
            load_intraday_experiment_artifact(
                context.artifact_root
                / f"intraday_walk_forward_{terminal.artifact_sha256s[0]}.json"
            ),
            False,
        )
    if len(events) != 1:
        raise GeneratedIntradayTrialError("invalid_trial_event_chain")
    try:
        first = _evaluate(context)
        replay = _evaluate(context)
        if (
            first.signal_stream_sha256 != replay.signal_stream_sha256
            or first.model_dump(exclude={"peak_rss_gib"})
            != replay.model_dump(exclude={"peak_rss_gib"})
        ):
            raise GeneratedIntradayTrialError("non_deterministic_strategy")
    except GeneratedStrategyExecutionError as error:
        kind = (
            TrialEventKind.CENSORED
            if error.reason in {"sandbox_preflight_failed", "sandbox_runtime_unavailable"}
            else TrialEventKind.FAILED
        )
        _append_terminal(ledger, registration, started, kind, error.reason)
        raise GeneratedIntradayTrialError(error.reason) from None
    except GeneratedIntradayEvaluationError as error:
        _append_terminal(ledger, registration, started, TrialEventKind.FAILED, error.reason)
        raise GeneratedIntradayTrialError(error.reason) from None
    except GeneratedIntradayTrialError as error:
        _append_terminal(ledger, registration, started, TrialEventKind.FAILED, error.reason)
        raise
    artifact = intraday_experiment_artifact(
        IntradayExperimentPayload(
            schema_version=3,
            trial_id=registration.trial_id,
            strategy_version=registration.strategy_version,
            evaluator_version=registration.evaluator_version,
            data_version=context.data_version,
            manifest_sha256=context.manifest_sha256,
            registered_at=registration.registered_at,
            started_at=started.occurred_at,
            completed_at=context.manifest.registered_at + dt.timedelta(seconds=3),
            result=first,
        )
    )
    _, created = publish_intraday_experiment_artifact(context.artifact_root, artifact)
    _append_completed(ledger, registration, started, artifact)
    return artifact, created


def _evaluate(context: GeneratedIntradayTrialContext):
    with tempfile.TemporaryDirectory(prefix="generated-intraday-") as temporary:
        return run_generated_intraday_walk_forward(
            GeneratedIntradayEvaluationRequest(
                bars=context.bars,
                strategy=context.published,
                sandbox=context.sandbox,
                minimum_training_sessions=context.manifest.minimum_training_sessions,
                per_side_cost_bps=context.manifest.per_side_total_cost_bps,
                bootstrap_samples=context.manifest.bootstrap_samples,
                rss_limit_gib=context.manifest.rss_limit_gib,
            ),
            Path(temporary),
        )


def _registration(
    context: GeneratedIntradayTrialContext,
    selection: GeneratedStrategySelection,
) -> ExperimentTrialRegistration:
    reader = ExperimentLedgerReader(context.experiment_ledger)
    versions = tuple(
        stored.registration
        for stored in reader.strategy_versions()
        if stored.registration.strategy_version == selection.strategy_version
    )
    cards = tuple(
        stored.card
        for stored in reader.research_hypothesis_cards()
        if stored.card.hypothesis.hypothesis_id == selection.hypothesis_id
    )
    if len(versions) != 1 or len(cards) != 1:
        raise GeneratedIntradayTrialError("strategy_registration_missing")
    seed = ":".join(
        (
            selection.strategy_version,
            context.data_version,
            context.manifest_sha256,
            selection.runtime_fingerprint,
        )
    )
    registered_at = context.manifest.registered_at + dt.timedelta(seconds=1)
    planned = _next_regular_session(registered_at)
    return ExperimentTrialRegistration(
        trial_id=f"generated-{hashlib.sha256(seed.encode()).hexdigest()[:16]}",
        strategy_version=selection.strategy_version,
        trial_kind=TrialKind.HISTORICAL_REPLAY,
        experiment_scope=cards[0].hypothesis.experiment_scope,
        experiment_scope_key=cards[0].hypothesis.experiment_scope_key,
        evaluator_version=context.manifest.evaluator_version,
        data_version=context.data_version,
        feed_entitlement="bounded local completed minute bars; no provider or broker access",
        planned_start=planned,
        planned_end=planned,
        registered_at=registered_at,
        evidence_budget=tuple(
            sorted(
                (
                    f"data_foundation_sha256:{selection.data_foundation_sha256}",
                    f"max_bars:{context.manifest.max_bars}",
                    f"max_sessions:{context.manifest.max_sessions}",
                    f"rss_limit_gib:{context.manifest.rss_limit_gib}",
                    f"runtime_fingerprint:{selection.runtime_fingerprint}",
                )
            )
        ),
    )


def _append_terminal(
    ledger: ExperimentLedgerStore,
    registration: ExperimentTrialRegistration,
    started: ExperimentTrialEvent,
    kind: TrialEventKind,
    reason: str,
) -> None:
    event = ExperimentTrialEvent(
        trial_id=registration.trial_id,
        sequence=2,
        event_kind=kind,
        occurred_at=started.occurred_at + dt.timedelta(seconds=1),
        artifact_sha256s=(),
        reason_codes=(reason,),
        previous_event_key=str(experiment_trial_event_key(started)),
    )
    with ledger.writer() as writer:
        _ = writer.append_trial_event(event)


def _append_completed(
    ledger: ExperimentLedgerStore,
    registration: ExperimentTrialRegistration,
    started: ExperimentTrialEvent,
    artifact: IntradayExperimentArtifact,
) -> None:
    event = ExperimentTrialEvent(
        trial_id=registration.trial_id,
        sequence=2,
        event_kind=TrialEventKind.COMPLETED,
        occurred_at=artifact.payload.completed_at,
        artifact_sha256s=(artifact.artifact_id,),
        reason_codes=(),
        previous_event_key=str(experiment_trial_event_key(started)),
    )
    with ledger.writer() as writer:
        _ = writer.append_trial_event(event)


def _next_regular_session(recorded_at: dt.datetime) -> dt.date:
    decision_date = recorded_at.astimezone(NEW_YORK).date()
    for offset in range(1, 11):
        candidate = decision_date + dt.timedelta(days=offset)
        if regular_session_bounds(candidate) is not None:
            return candidate
    raise GeneratedIntradayTrialError("regular_session_not_found")

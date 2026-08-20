from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from pathlib import Path

from tests.day_strategy_capsule_support import SHA_A, builtin_request, no_signal_source, proposal
from trading_agent.day_discovery_hypothesis_factory import (
    DayHypothesisBuildInput,
    build_day_hypothesis_contracts,
)
from trading_agent.day_historical_evidence import (
    DayEvidenceWindow,
    DayHistoricalEvidenceRequest,
    DayHistoricalPreregistration,
    DayMarketCostEvaluator,
    DayPointInTimeDataManifest,
    DaySelectionDiagnostics,
)
from trading_agent.day_research_attempt_binding import DayResearchAttemptBinding
from trading_agent.day_strategy_capsule import publish_day_strategy_capsule
from trading_agent.day_strategy_capsule_models import StrategyCapsule
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.intraday_overfit_diagnostics_models import IntradayOverfitDiagnosticsStatus
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_ledger import ExactHoldoutMetric, HoldoutReveal
from trading_agent.strategy_research_models import PreregistrationManifest
from trading_agent.strategy_research_results import ResearchAttempt, TerminalResearchResult
from trading_agent.strategy_research_types import (
    AttemptStatus,
    SafeTerminalReason,
    TerminalOutcome,
)


@dataclass(frozen=True, slots=True)
class CompletedDayEvidenceContext:
    store: ExperimentLedgerStore
    manifest: PreregistrationManifest
    capsule: StrategyCapsule
    reveal_id: str


def completed_day_evidence_context(tmp_path: Path) -> CompletedDayEvidenceContext:
    observed_at = dt.datetime(2026, 8, 20, 14, 0, tzinfo=dt.UTC)
    family, version, generated_manifest = build_day_hypothesis_contracts(
        proposal(no_signal_source()),
        DayHypothesisBuildInput(
            market_id=MarketId.US_EQUITIES,
            observed_at=observed_at,
            completed_bar_at=observed_at,
            first_eligible_completed_bar_at=observed_at + dt.timedelta(minutes=5),
            universe_snapshot_id="us-equities-bounded-20260820",
            universe_snapshot_at=observed_at - dt.timedelta(minutes=1),
            source_refs=("fixture:bounded-bars",),
            data_manifest_sha256=SHA_A,
            search_budget=1,
        ),
        terminal=True,
    )
    manifest = PreregistrationManifest.model_validate(generated_manifest.model_dump(mode="python"))
    historical_preregistration = _historical_preregistration(manifest)
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    hypothesis = manifest.hypothesis
    attempt = ResearchAttempt(
        attempt_id="attempt-day-1",
        hypothesis_id=hypothesis.hypothesis_id,
        branch_index=0,
        input_hashes=(SHA_A, historical_preregistration.content_sha256),
        code_sha256=version.code_sha256,
        data_manifest_sha256=version.data_manifest_sha256,
        started_at=observed_at + dt.timedelta(minutes=6),
        finished_at=observed_at + dt.timedelta(minutes=7),
        status=AttemptStatus.SUCCEEDED,
        artifact_refs=(f"artifact://safe/{version.code_sha256}",),
        error_class=None,
        max_cpu_seconds=60,
    )
    binding_payload = {
        "binding_id": "",
        "attempt_id": attempt.attempt_id,
        "market_id": version.market_id,
        "hypothesis_version_id": version.hypothesis_version_id,
        "artifact_ref": attempt.artifact_refs[0],
        "multiple_testing_family": version.multiple_testing_family,
        "multiple_testing_budget": version.search_budget.max_attempts,
        "search_budget_debit": 1,
        "bound_at": observed_at + dt.timedelta(minutes=8),
    }
    binding = DayResearchAttemptBinding.model_validate(
        binding_payload | {"binding_id": DayResearchAttemptBinding.canonical_id_for(binding_payload)}
    )
    with store.writer() as writer:
        _ = writer.register_strategy_research(manifest)
        _ = writer.append_strategy_research_attempt(attempt)
        _ = writer.register_day_hypothesis_family(family)
        _ = writer.register_day_hypothesis_version(version)
        _ = writer.register_day_research_attempt_binding(binding)
        terminal = TerminalResearchResult(
            result_id="terminal-day-1",
            hypothesis_id=hypothesis.hypothesis_id,
            owner_agent_id=hypothesis.agent_id,
            outcome=TerminalOutcome.SUPPORTED,
            reason_codes=(SafeTerminalReason.PREREGISTERED_SUPPORT_MET,),
            artifact_refs=(f"artifact://safe/{SHA_A}",),
            evaluated_at=observed_at + dt.timedelta(minutes=9),
        )
        reveal = HoldoutReveal(
            reveal_id="reveal-day-1",
            hypothesis_id=hypothesis.hypothesis_id,
            seal_id=hypothesis.holdout_period_sealed_ref.seal_id,
            commitment_sha256=hypothesis.holdout_period_sealed_ref.commitment_sha256,
            reviewer_id="independent-reviewer-v1",
            exact_metrics=(ExactHoldoutMetric(name="net_return", value=0.03, lower=0.01, upper=0.05),),
            sanitized_result=terminal,
            revealed_at=terminal.evaluated_at,
        )
        _ = writer.reveal_strategy_research_holdout(reveal)
    capsule, _ = publish_day_strategy_capsule(
        store,
        replace(
            builtin_request(),
            hypothesis_version_id=version.hypothesis_version_id,
            attempt_binding_id=binding.binding_id,
            market_id=version.market_id,
            artifact_ref=binding.artifact_ref,
            artifact_sha256=version.code_sha256,
            evaluation_cadence=version.evaluation_cadence,
            entry_rule=version.entry_rule,
            exit_rule=version.exit_rule,
            stop_rule=version.stop_rule,
            cost_model=version.cost_model,
            slippage_model_id=hypothesis.slippage_model_id,
            protocol_sha256=version.protocol_sha256,
            evaluator_sha256=SHA_A,
            published_at=observed_at + dt.timedelta(minutes=10),
        ),
    )
    return CompletedDayEvidenceContext(store, manifest, capsule, reveal.reveal_id)


def historical_evidence_request(
    context: CompletedDayEvidenceContext,
) -> DayHistoricalEvidenceRequest:
    hypothesis = context.manifest.hypothesis
    capsule = context.capsule
    attempts = context.store.reader().day_attempts_for_review(
        capsule.market_id,
        capsule.hypothesis_version_id,
    )
    diagnostics_sha256 = "c" * 64
    diagnostics = DaySelectionDiagnostics(
        market_id=capsule.market_id,
        input_attempt_ids=tuple(sorted(item.attempt.attempt_id for item in attempts)),
        total_attempted_variants=len(attempts),
        status=IntradayOverfitDiagnosticsStatus.COLLECTING,
        diagnostics_artifact_ref=f"artifact://safe/{diagnostics_sha256}",
        diagnostics_sha256=diagnostics_sha256,
    )
    preregistration = _historical_preregistration(context.manifest)
    return DayHistoricalEvidenceRequest(
        ledger=context.store.reader(),
        capsule=capsule,
        preregistration=preregistration,
        data_manifest=DayPointInTimeDataManifest(
            market_id=capsule.market_id,
            data_manifest_sha256=hypothesis.data_manifest_sha256,
            universe_snapshot_id=hypothesis.universe_snapshot_id,
            point_in_time_as_of=hypothesis.universe_observed_at,
            source_kind=hypothesis.source_refs[0].source_kind,
            evidence_use=hypothesis.evidence_use,
            full_universe=False,
        ),
        cost_evaluator=DayMarketCostEvaluator(
            market_id=capsule.market_id,
            cost_model_id=hypothesis.cost_model_id,
            slippage_model_id=hypothesis.slippage_model_id,
            evaluator_sha256=capsule.evaluator_sha256,
        ),
        selection_diagnostics=diagnostics,
        evaluated_at=dt.datetime(2026, 8, 20, 15, 0, tzinfo=dt.UTC),
        next_review_date=dt.date(2026, 8, 21),
        artifact_refs=(f"artifact://safe/{SHA_A}",),
    )


def _historical_preregistration(
    manifest: PreregistrationManifest,
) -> DayHistoricalPreregistration:
    hypothesis = manifest.hypothesis
    return DayHistoricalPreregistration(
        preregistration_sha256=manifest.content_sha256,
        holdout_seal_id=hypothesis.holdout_period_sealed_ref.seal_id,
        holdout_commitment_sha256=hypothesis.holdout_period_sealed_ref.commitment_sha256,
        preregistered_at=manifest.preregistered_at,
        train=DayEvidenceWindow.model_validate(hypothesis.train_period.model_dump()),
        validation=DayEvidenceWindow.model_validate(hypothesis.validation_period.model_dump()),
        sealed_holdout=DayEvidenceWindow(
            start=hypothesis.validation_period.end + dt.timedelta(days=2),
            end=hypothesis.validation_period.end + dt.timedelta(days=4),
        ),
        purge=dt.timedelta(days=1),
        embargo=dt.timedelta(days=2),
        power_or_ci_gate=hypothesis.power_or_ci_gate,
    )


__all__ = (
    "CompletedDayEvidenceContext",
    "completed_day_evidence_context",
    "historical_evidence_request",
)

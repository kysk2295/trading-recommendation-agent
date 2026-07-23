from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.intraday_actual_research_audit_inputs import (
    AuditedBindingEvidence,
    AuditedDatasetEvidence,
)
from trading_agent.intraday_actual_research_audit_models import (
    IntradayActualResearchAuditError,
)
from trading_agent.intraday_actual_research_plan_models import (
    IntradayActualResearchRunPlan,
)
from trading_agent.intraday_research_artifacts import (
    InvalidIntradayResearchArtifactError,
    load_intraday_experiment_artifact,
)
from trading_agent.intraday_research_loop_models import IntradayReviewerDecision
from trading_agent.intraday_research_reviewer import (
    IntradayReviewArtifact,
    InvalidIntradayResearchReviewError,
    evaluate_intraday_experiment,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    read_private_text,
)


@dataclass(frozen=True, slots=True)
class AuditedTrialEvidence:
    trial_ids: tuple[str, ...]
    experiment_artifact_ids: tuple[str, ...]
    review_artifact_ids: tuple[str, ...]
    reviewer_decisions: tuple[IntradayReviewerDecision, ...]


def load_actual_research_trials(
    plan: IntradayActualResearchRunPlan,
    dataset: AuditedDatasetEvidence,
    binding: AuditedBindingEvidence,
) -> AuditedTrialEvidence:
    try:
        return _load_trials(plan, dataset, binding)
    except IntradayActualResearchAuditError:
        raise
    except (
        InvalidExperimentLedgerSourceError,
        InvalidIntradayResearchArtifactError,
        InvalidIntradayResearchReviewError,
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise IntradayActualResearchAuditError("invalid_trial_evidence") from None


def _load_trials(
    plan: IntradayActualResearchRunPlan,
    dataset: AuditedDatasetEvidence,
    binding: AuditedBindingEvidence,
) -> AuditedTrialEvidence:
    spec = plan.content.spec
    reader = ExperimentLedgerReader(spec.paths.experiment_ledger)
    reviews = tuple(
        _load_review(path)
        for path in _bounded_paths(
            spec.paths.review_root,
            "intraday_research_review_*.json",
        )
    )
    trials = []
    experiments = []
    matched_reviews = []
    for hypothesis in binding.manifest.hypotheses:
        matching = tuple(
            item.registration
            for item in reader.trials()
            if item.registration.strategy_version == hypothesis.strategy_version
            and item.registration.data_version == dataset.input_sha256
        )
        if len(matching) != 1:
            raise IntradayActualResearchAuditError("trial_identity_mismatch")
        trial = matching[0]
        events = reader.trial_events(trial.trial_id)
        if (
            len(events) != 2
            or events[0].event.event_kind is not TrialEventKind.STARTED
            or events[1].event.event_kind is not TrialEventKind.COMPLETED
            or len(events[1].event.artifact_sha256s) != 1
        ):
            raise IntradayActualResearchAuditError("trial_terminal_mismatch")
        experiment_id = events[1].event.artifact_sha256s[0]
        experiment = load_intraday_experiment_artifact(
            spec.paths.artifact_root / f"intraday_walk_forward_{experiment_id}.json"
        )
        review_matches = tuple(
            item
            for item in reviews
            if item.payload.trial_id == trial.trial_id
            and item.payload.experiment_artifact_id == experiment_id
        )
        if (
            len(review_matches) != 1
            or experiment.payload.trial_id != trial.trial_id
            or experiment.payload.strategy_version != trial.strategy_version
            or experiment.payload.evaluator_version
            != trial.evaluator_version
            or experiment.payload.data_version != dataset.input_sha256
            or experiment.payload.manifest_sha256 != binding.manifest_sha256
            or experiment.payload.result.strategy is not hypothesis.strategy
            or experiment.payload.result.side_cost_bps
            != binding.manifest.per_side_total_cost_bps
        ):
            raise IntradayActualResearchAuditError("review_or_experiment_mismatch")
        review = review_matches[0]
        expected_review = evaluate_intraday_experiment(
            reader,
            experiment,
            review.payload.reviewed_at,
        )
        if review.payload != expected_review:
            raise IntradayActualResearchAuditError(
                "review_or_experiment_mismatch"
            )
        trials.append(trial)
        experiments.append(experiment)
        matched_reviews.append(review)
    return AuditedTrialEvidence(
        trial_ids=tuple(item.trial_id for item in trials),
        experiment_artifact_ids=tuple(item.artifact_id for item in experiments),
        review_artifact_ids=tuple(item.artifact_id for item in matched_reviews),
        reviewer_decisions=tuple(item.payload.decision for item in matched_reviews),
    )


def _load_review(path: Path) -> IntradayReviewArtifact:
    raw = read_private_text(path)
    artifact = IntradayReviewArtifact.model_validate_json(raw)
    if (
        path.name != f"intraday_research_review_{artifact.artifact_id}.json"
        or raw != canonical_experiment_ledger_json(artifact) + "\n"
    ):
        raise IntradayActualResearchAuditError("review_artifact_not_canonical")
    return artifact


def _bounded_paths(root: Path, pattern: str) -> tuple[Path, ...]:
    paths = tuple(sorted(root.glob(pattern)))
    if len(paths) > 1_000:
        raise IntradayActualResearchAuditError("artifact_scan_budget_exceeded")
    return paths


__all__ = (
    "AuditedTrialEvidence",
    "load_actual_research_trials",
)

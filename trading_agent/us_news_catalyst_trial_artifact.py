from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.us_news_catalyst_trial_models import (
    InvalidUsNewsCatalystTrialModelError,
    UsNewsCatalystCohortArtifact,
)
from trading_agent.us_news_catalyst_trial_outcome_models import (
    UsNewsCatalystSetupObservationManifest,
    UsNewsCatalystTrialOutcomeArtifact,
)


def publish_us_news_catalyst_cohort(
    root: Path,
    artifact: UsNewsCatalystCohortArtifact,
) -> tuple[Path, bool]:
    return _publish(
        root,
        "us_news_catalyst_cohort",
        artifact.artifact_id,
        artifact,
        UsNewsCatalystCohortArtifact,
    )


def publish_us_news_catalyst_setup_observation_manifest(
    root: Path,
    manifest: UsNewsCatalystSetupObservationManifest,
) -> tuple[Path, bool]:
    return _publish(
        root,
        "us_news_catalyst_setup",
        manifest.manifest_id,
        manifest,
        UsNewsCatalystSetupObservationManifest,
    )


def publish_us_news_catalyst_outcome(
    root: Path,
    artifact: UsNewsCatalystTrialOutcomeArtifact,
) -> tuple[Path, bool]:
    return _publish(
        root,
        "us_news_catalyst_outcome",
        artifact.artifact_id,
        artifact,
        UsNewsCatalystTrialOutcomeArtifact,
    )


def load_us_news_catalyst_cohort(path: Path) -> UsNewsCatalystCohortArtifact:
    return _load(
        path,
        "us_news_catalyst_cohort",
        UsNewsCatalystCohortArtifact,
        lambda value: value.artifact_id,
    )


def load_us_news_catalyst_setup_observation_manifest(
    path: Path,
) -> UsNewsCatalystSetupObservationManifest:
    return _load(
        path,
        "us_news_catalyst_setup",
        UsNewsCatalystSetupObservationManifest,
        lambda value: value.manifest_id,
    )


def load_us_news_catalyst_outcome(path: Path) -> UsNewsCatalystTrialOutcomeArtifact:
    return _load(
        path,
        "us_news_catalyst_outcome",
        UsNewsCatalystTrialOutcomeArtifact,
        lambda value: value.artifact_id,
    )


def cohorts_in(root: Path) -> tuple[UsNewsCatalystCohortArtifact, ...]:
    return tuple(
        load_us_news_catalyst_cohort(path)
        for path in sorted(root.glob("us_news_catalyst_cohort_*.json"))
    ) if root.is_dir() else ()


def outcomes_in(root: Path) -> tuple[UsNewsCatalystTrialOutcomeArtifact, ...]:
    return tuple(
        load_us_news_catalyst_outcome(path)
        for path in sorted(root.glob("us_news_catalyst_outcome_*.json"))
    ) if root.is_dir() else ()


def setup_manifests_in(root: Path) -> tuple[UsNewsCatalystSetupObservationManifest, ...]:
    return tuple(
        load_us_news_catalyst_setup_observation_manifest(path)
        for path in sorted(root.glob("us_news_catalyst_setup_*.json"))
    ) if root.is_dir() else ()


def _publish[ModelT: BaseModel](
    root: Path,
    prefix: str,
    identity: str,
    value: ModelT,
    model_type: type[ModelT],
) -> tuple[Path, bool]:
    try:
        checked = model_type.model_validate(value.model_dump())
        path = root / f"{prefix}_{identity}.json"
        created = publish_private_immutable_text(path, _payload(checked))
        return path, created
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise InvalidUsNewsCatalystTrialModelError from None


def _load[ModelT: BaseModel](
    path: Path,
    prefix: str,
    model_type: type[ModelT],
    identity_of: Callable[[ModelT], str],
) -> ModelT:
    try:
        payload = read_private_text(path)
        value = model_type.model_validate_json(payload)
        identity = identity_of(value)
        if path.name != f"{prefix}_{identity}.json" or payload != _payload(value):
            raise InvalidUsNewsCatalystTrialModelError
        return value
    except InvalidUsNewsCatalystTrialModelError:
        raise
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise InvalidUsNewsCatalystTrialModelError from None


def _payload(value: BaseModel) -> str:
    return canonical_experiment_ledger_json(value) + "\n"


__all__ = (
    "cohorts_in",
    "load_us_news_catalyst_cohort",
    "load_us_news_catalyst_outcome",
    "load_us_news_catalyst_setup_observation_manifest",
    "outcomes_in",
    "publish_us_news_catalyst_cohort",
    "publish_us_news_catalyst_outcome",
    "publish_us_news_catalyst_setup_observation_manifest",
    "setup_manifests_in",
)

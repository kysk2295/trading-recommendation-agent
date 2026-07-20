from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.us_news_catalyst_feature_models import (
    InvalidUsNewsCatalystFeatureModelError,
    UsNewsCatalystFeatureArtifact,
)

_PREFIX = "us_news_catalyst_feature"


def publish_us_news_catalyst_feature_artifact(
    root: Path,
    artifact: UsNewsCatalystFeatureArtifact,
) -> tuple[Path, bool]:
    try:
        checked = UsNewsCatalystFeatureArtifact.model_validate(artifact.model_dump())
        path = root / f"{_PREFIX}_{checked.artifact_id}.json"
        return path, publish_private_immutable_text(path, _payload(checked))
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise InvalidUsNewsCatalystFeatureModelError from None


def load_us_news_catalyst_feature_artifact(
    path: Path,
) -> UsNewsCatalystFeatureArtifact:
    try:
        payload = read_private_text(path)
        artifact = UsNewsCatalystFeatureArtifact.model_validate_json(payload)
        if path.name != f"{_PREFIX}_{artifact.artifact_id}.json" or payload != _payload(
            artifact
        ):
            raise InvalidUsNewsCatalystFeatureModelError
        return artifact
    except InvalidUsNewsCatalystFeatureModelError:
        raise
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise InvalidUsNewsCatalystFeatureModelError from None


def feature_artifacts_in(root: Path) -> tuple[UsNewsCatalystFeatureArtifact, ...]:
    if not root.is_dir():
        return ()
    return tuple(
        load_us_news_catalyst_feature_artifact(path)
        for path in sorted(root.glob(f"{_PREFIX}_*.json"))
    )


def _payload(artifact: UsNewsCatalystFeatureArtifact) -> str:
    return canonical_experiment_ledger_json(artifact) + "\n"


__all__ = (
    "feature_artifacts_in",
    "load_us_news_catalyst_feature_artifact",
    "publish_us_news_catalyst_feature_artifact",
)

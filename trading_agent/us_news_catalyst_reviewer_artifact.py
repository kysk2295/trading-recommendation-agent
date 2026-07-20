from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.us_news_catalyst_reviewer_models import (
    InvalidUsNewsCatalystReviewerModelError,
    UsNewsCatalystReviewArtifact,
)


def publish_us_news_catalyst_review(
    root: Path,
    artifact: UsNewsCatalystReviewArtifact,
) -> tuple[Path, bool]:
    try:
        checked = UsNewsCatalystReviewArtifact.model_validate(artifact.model_dump())
        path = root / f"us_news_catalyst_review_{checked.artifact_id}.json"
        created = publish_private_immutable_text(path, _payload(checked))
        return path, created
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise InvalidUsNewsCatalystReviewerModelError from None


def load_us_news_catalyst_review(path: Path) -> UsNewsCatalystReviewArtifact:
    try:
        payload = read_private_text(path)
        artifact = UsNewsCatalystReviewArtifact.model_validate_json(payload)
        if (
            path.name != f"us_news_catalyst_review_{artifact.artifact_id}.json"
            or payload != _payload(artifact)
        ):
            raise InvalidUsNewsCatalystReviewerModelError
        return artifact
    except InvalidUsNewsCatalystReviewerModelError:
        raise
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise InvalidUsNewsCatalystReviewerModelError from None


def reviews_in(root: Path) -> tuple[UsNewsCatalystReviewArtifact, ...]:
    return tuple(
        load_us_news_catalyst_review(path)
        for path in sorted(root.glob("us_news_catalyst_review_*.json"))
    ) if root.is_dir() else ()


def _payload(artifact: UsNewsCatalystReviewArtifact) -> str:
    return canonical_experiment_ledger_json(artifact) + "\n"


__all__ = (
    "load_us_news_catalyst_review",
    "publish_us_news_catalyst_review",
    "reviews_in",
)

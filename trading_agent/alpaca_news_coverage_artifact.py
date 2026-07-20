from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from trading_agent.alpaca_news_coverage_models import (
    AlpacaNewsCoverageArtifact,
    AlpacaNewsCoverageContractError,
    AlpacaNewsCoverageManifest,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.private_query_file import (
    InvalidPrivateQueryFileError,
    read_private_text_query_only,
)


def load_alpaca_news_coverage_manifest(path: Path) -> AlpacaNewsCoverageManifest:
    try:
        return AlpacaNewsCoverageManifest.model_validate_json(
            read_private_text_query_only(path)
        )
    except (InvalidPrivateQueryFileError, TypeError, ValidationError, ValueError):
        raise AlpacaNewsCoverageContractError from None


def publish_alpaca_news_coverage_artifact(
    root: Path,
    artifact: AlpacaNewsCoverageArtifact,
) -> tuple[Path, bool]:
    try:
        checked = AlpacaNewsCoverageArtifact.model_validate(artifact.model_dump())
        path = root / f"alpaca_news_coverage_{checked.artifact_id}.json"
        created = publish_private_immutable_text(path, _payload(checked))
        return path, created
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise AlpacaNewsCoverageContractError from None


def load_alpaca_news_coverage_artifact(path: Path) -> AlpacaNewsCoverageArtifact:
    try:
        payload = read_private_text(path)
        artifact = AlpacaNewsCoverageArtifact.model_validate_json(payload)
        if (
            path.name != f"alpaca_news_coverage_{artifact.artifact_id}.json"
            or payload != _payload(artifact)
        ):
            raise AlpacaNewsCoverageContractError
        return artifact
    except AlpacaNewsCoverageContractError:
        raise
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise AlpacaNewsCoverageContractError from None


def _payload(artifact: AlpacaNewsCoverageArtifact) -> str:
    return canonical_experiment_ledger_json(artifact) + "\n"


__all__ = (
    "load_alpaca_news_coverage_artifact",
    "load_alpaca_news_coverage_manifest",
    "publish_alpaca_news_coverage_artifact",
)

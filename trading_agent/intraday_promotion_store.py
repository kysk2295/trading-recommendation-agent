from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import override

from pydantic import BaseModel, ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.intraday_promotion_models import (
    IntradayPromotionApproval,
    IntradayPromotionAssessment,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)


@dataclass(frozen=True, slots=True)
class InvalidIntradayPromotionArtifactError(ValueError):
    @override
    def __str__(self) -> str:
        return "intraday promotion artifact is invalid"


def publish_promotion_assessment(
    root: Path,
    assessment: IntradayPromotionAssessment,
) -> tuple[Path, bool]:
    path = root / f"intraday_promotion_assessment_{assessment.assessment_id}.json"
    return path, _publish(path, assessment)


def load_promotion_assessment(path: Path) -> IntradayPromotionAssessment:
    artifact = _load(path, IntradayPromotionAssessment)
    if path.name != f"intraday_promotion_assessment_{artifact.assessment_id}.json":
        raise InvalidIntradayPromotionArtifactError
    return artifact


def publish_promotion_approval(
    root: Path,
    approval: IntradayPromotionApproval,
) -> tuple[Path, bool]:
    path = root / f"intraday_promotion_approval_{approval.approval_id}.json"
    return path, _publish(path, approval)


def load_promotion_approval(path: Path) -> IntradayPromotionApproval:
    artifact = _load(path, IntradayPromotionApproval)
    if path.name != f"intraday_promotion_approval_{artifact.approval_id}.json":
        raise InvalidIntradayPromotionArtifactError
    return artifact


def load_canonical_artifact[ArtifactT: BaseModel](
    path: Path,
    artifact_type: type[ArtifactT],
    expected_prefix: str,
) -> ArtifactT:
    artifact = _load(path, artifact_type)
    artifact_identifier = getattr(artifact, "artifact_id", None)
    if not isinstance(artifact_identifier, str) or path.name != f"{expected_prefix}_{artifact_identifier}.json":
        raise InvalidIntradayPromotionArtifactError
    return artifact


def require_private_authoritative_file(path: Path) -> None:
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise InvalidIntradayPromotionArtifactError
    except OSError:
        raise InvalidIntradayPromotionArtifactError from None


def _publish(path: Path, artifact: BaseModel) -> bool:
    try:
        return publish_private_immutable_text(
            path,
            canonical_experiment_ledger_json(artifact) + "\n",
        )
    except InvalidPrivateImmutableFileError:
        raise InvalidIntradayPromotionArtifactError from None


def _load[ArtifactT: BaseModel](path: Path, artifact_type: type[ArtifactT]) -> ArtifactT:
    try:
        require_private_authoritative_file(path)
        encoded = read_private_text(path)
        artifact = artifact_type.model_validate_json(encoded)
        if encoded != canonical_experiment_ledger_json(artifact) + "\n":
            raise InvalidIntradayPromotionArtifactError
        return artifact
    except InvalidIntradayPromotionArtifactError:
        raise
    except (
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidIntradayPromotionArtifactError from None


__all__ = (
    "InvalidIntradayPromotionArtifactError",
    "load_canonical_artifact",
    "load_promotion_approval",
    "load_promotion_assessment",
    "publish_promotion_approval",
    "publish_promotion_assessment",
    "require_private_authoritative_file",
)

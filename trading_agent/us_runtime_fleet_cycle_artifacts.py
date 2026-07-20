from __future__ import annotations

from pathlib import Path
from typing import override

from trading_agent.research_evidence_artifact import (
    ResearchEvidenceArtifactError,
    write_research_evidence_artifact,
)
from trading_agent.us_feature_evidence_models import UsFeatureEvidenceBinding
from trading_agent.us_news_catalyst_feature_artifact import (
    publish_us_news_catalyst_feature_artifact,
)
from trading_agent.us_news_catalyst_feature_models import (
    InvalidUsNewsCatalystFeatureModelError,
)
from trading_agent.us_news_catalyst_feature_projection import (
    InvalidUsNewsCatalystFeatureProjectionError,
    project_us_news_catalyst_feature_artifact,
)
from trading_agent.us_sip_research_evidence_projection import (
    UsSipResearchEvidenceProjectionError,
    project_us_sip_research_evidence,
)


class RuntimeFleetArtifactProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "runtime fleet artifact projection is blocked"


def write_runtime_research_artifacts(
    bindings: tuple[UsFeatureEvidenceBinding, ...],
    canonical_root: Path,
    artifact_root: Path | None,
    minimum_rvol_bps: int,
) -> tuple[int, int] | None:
    if artifact_root is None:
        return None
    try:
        models = project_us_sip_research_evidence(
            bindings,
            canonical_root,
            minimum_rvol_bps=minimum_rvol_bps,
        )
        created = tuple(
            write_research_evidence_artifact(artifact_root, model)[1] for model in models
        )
        return sum(created), len(created) - sum(created)
    except (ResearchEvidenceArtifactError, UsSipResearchEvidenceProjectionError):
        raise RuntimeFleetArtifactProjectionError from None


def write_us_news_catalyst_feature_artifacts(
    bindings: tuple[UsFeatureEvidenceBinding, ...],
    artifact_root: Path | None,
) -> tuple[int, int] | None:
    if artifact_root is None:
        return None
    try:
        created = tuple(
            publish_us_news_catalyst_feature_artifact(
                artifact_root,
                project_us_news_catalyst_feature_artifact(binding),
            )[1]
            for binding in bindings
        )
        return sum(created), len(created) - sum(created)
    except (
        InvalidUsNewsCatalystFeatureModelError,
        InvalidUsNewsCatalystFeatureProjectionError,
    ):
        raise RuntimeFleetArtifactProjectionError from None


__all__ = (
    "RuntimeFleetArtifactProjectionError",
    "write_runtime_research_artifacts",
    "write_us_news_catalyst_feature_artifacts",
)

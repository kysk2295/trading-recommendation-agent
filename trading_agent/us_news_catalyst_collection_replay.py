from __future__ import annotations

from pathlib import Path
from typing import override

from trading_agent.us_news_catalyst_collection_models import (
    UsNewsCatalystCollectionPlan,
    UsNewsCatalystCollectionReceipt,
)
from trading_agent.us_news_catalyst_feature_artifact import feature_artifacts_in


class InvalidUsNewsCatalystCollectionReplayError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst collection replay is blocked"


def validate_collection_receipt(
    receipt: UsNewsCatalystCollectionReceipt,
    plan: UsNewsCatalystCollectionPlan,
    feature_root: Path,
) -> None:
    content = receipt.content
    expected = tuple(item.symbol for item in plan.content.bindings)
    if (
        content.plan_id != plan.plan_id
        or content.cohort_artifact_id != plan.content.cohort_artifact_id
        or content.evaluated_at != plan.content.evaluated_at
        or tuple(item.symbol for item in content.features) != expected
    ):
        raise InvalidUsNewsCatalystCollectionReplayError
    artifacts = {item.artifact_id: item for item in feature_artifacts_in(feature_root)}
    bindings = {item.symbol: item for item in plan.content.bindings}
    try:
        for reference in content.features:
            artifact = artifacts[reference.artifact_id]
            binding = bindings[reference.symbol]
            if (
                artifact.payload.symbol != reference.symbol
                or artifact.payload.instrument_id != binding.instrument_id
                or artifact.payload.session_date != plan.content.session_date
                or artifact.payload.observed_at != plan.content.evaluated_at
                or artifact.payload.volume_profile_evidence_sha256
                != binding.profile_evidence_sha256
            ):
                raise InvalidUsNewsCatalystCollectionReplayError
    except KeyError:
        raise InvalidUsNewsCatalystCollectionReplayError from None


__all__ = (
    "InvalidUsNewsCatalystCollectionReplayError",
    "validate_collection_receipt",
)

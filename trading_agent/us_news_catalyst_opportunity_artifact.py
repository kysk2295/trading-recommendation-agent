from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.us_news_catalyst_opportunity import (
    UsNewsCatalystOpportunityProjection,
    UsNewsCatalystProjectionError,
)


def publish_us_news_catalyst_opportunity_projection(
    root: Path,
    projection: UsNewsCatalystOpportunityProjection,
) -> tuple[Path, bool]:
    try:
        checked = UsNewsCatalystOpportunityProjection.model_validate(projection.model_dump())
        path = root / f"us_news_catalyst_projection_{checked.projection_id}.json"
        created = publish_private_immutable_text(path, _payload(checked))
        return path, created
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise UsNewsCatalystProjectionError from None


def load_us_news_catalyst_opportunity_projection(
    path: Path,
) -> UsNewsCatalystOpportunityProjection:
    try:
        payload = read_private_text(path)
        projection = UsNewsCatalystOpportunityProjection.model_validate_json(payload)
        if (
            path.name != f"us_news_catalyst_projection_{projection.projection_id}.json"
            or payload != _payload(projection)
        ):
            raise UsNewsCatalystProjectionError
        return projection
    except UsNewsCatalystProjectionError:
        raise
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise UsNewsCatalystProjectionError from None


def _payload(projection: UsNewsCatalystOpportunityProjection) -> str:
    return canonical_experiment_ledger_json(projection) + "\n"


__all__ = (
    "load_us_news_catalyst_opportunity_projection",
    "publish_us_news_catalyst_opportunity_projection",
)

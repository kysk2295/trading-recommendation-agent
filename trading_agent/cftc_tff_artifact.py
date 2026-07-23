from __future__ import annotations

from pathlib import Path
from typing import override

from pydantic import ValidationError

from trading_agent.cftc_tff_models import CftcTffPositioningContext
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)

_PREFIX = "cftc_tff_context"


class CftcTffArtifactError(ValueError):
    @override
    def __str__(self) -> str:
        return "CFTC TFF artifact is invalid"


def publish_cftc_tff_context(
    output_root: Path,
    context: CftcTffPositioningContext,
) -> tuple[Path, bool]:
    try:
        checked = CftcTffPositioningContext.model_validate(context.model_dump(mode="python"))
        path = output_root / f"{_PREFIX}_{checked.context_id}.json"
        created = publish_private_immutable_text(
            path,
            canonical_experiment_ledger_json(checked) + "\n",
        )
        return path, created
    except (
        InvalidPrivateImmutableFileError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise CftcTffArtifactError from None


__all__ = (
    "CftcTffArtifactError",
    "publish_cftc_tff_context",
)

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)
from trading_agent.treasury_yield_models import (
    TreasuryYieldContext,
    TreasuryYieldError,
)

_PREFIX = "treasury_yield_curve_context"


def publish_treasury_yield_context(
    output_root: Path,
    context: TreasuryYieldContext,
) -> tuple[Path, bool]:
    try:
        checked = TreasuryYieldContext.model_validate(
            context.model_dump(mode="python"),
        )
        path = output_root / f"{_PREFIX}_{checked.context_id}.json"
        created = publish_private_immutable_text(
            path,
            canonical_experiment_ledger_json(checked) + "\n",
        )
        return path, created
    except (
        InvalidPrivateImmutableFileError,
        TreasuryYieldError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise TreasuryYieldError from None


__all__ = ("publish_treasury_yield_context",)

from __future__ import annotations

from pathlib import Path
from typing import override

from pydantic import ValidationError

from trading_agent.bls_public_models import BlsMacroSnapshot
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)

_PREFIX = "bls_macro_snapshot"


class BlsPublicArtifactError(ValueError):
    @override
    def __str__(self) -> str:
        return "BLS public data artifact is invalid"


def publish_bls_macro_snapshot(
    output_root: Path,
    snapshot: BlsMacroSnapshot,
) -> tuple[Path, bool]:
    try:
        checked = BlsMacroSnapshot.model_validate(
            snapshot.model_dump(mode="python")
        )
        path = output_root / f"{_PREFIX}_{checked.snapshot_id}.json"
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
        raise BlsPublicArtifactError from None


__all__ = (
    "BlsPublicArtifactError",
    "publish_bls_macro_snapshot",
)

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from trading_agent.day_historical_evidence import (
    DayHistoricalEvidenceSeal,
    InvalidDayHistoricalEvidenceError,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)


def publish_day_historical_evidence(
    root: Path,
    seal: DayHistoricalEvidenceSeal,
) -> tuple[Path, bool]:
    try:
        checked = DayHistoricalEvidenceSeal.model_validate(seal.model_dump(mode="python"))
        path = root / f"day_historical_evidence_{checked.seal_id}.json"
        created = publish_private_immutable_text(path, _payload(checked))
        return path, created
    except (InvalidPrivateImmutableFileError, ValidationError):
        raise InvalidDayHistoricalEvidenceError from None


def load_day_historical_evidence(path: Path) -> DayHistoricalEvidenceSeal:
    try:
        raw = read_private_text(path)
        seal = DayHistoricalEvidenceSeal.model_validate_json(raw)
        if path.name != f"day_historical_evidence_{seal.seal_id}.json" or raw != _payload(seal):
            raise InvalidDayHistoricalEvidenceError
        return seal
    except InvalidDayHistoricalEvidenceError:
        raise
    except (InvalidPrivateImmutableFileError, ValidationError):
        raise InvalidDayHistoricalEvidenceError from None


def _payload(seal: DayHistoricalEvidenceSeal) -> str:
    return canonical_experiment_ledger_json(seal) + "\n"


__all__ = ("load_day_historical_evidence", "publish_day_historical_evidence")

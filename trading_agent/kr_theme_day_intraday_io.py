from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import override

from pydantic import ValidationError

from trading_agent.signal_contract_models import OpportunitySnapshot


class InvalidKrThemeDayOpportunitySourceError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day opportunity source is invalid"


def load_exact_kr_theme_opportunity(path: Path, opportunity_id: str) -> OpportunitySnapshot:
    try:
        _require_private_file(path)
        lines = path.read_text(encoding="utf-8").splitlines()
        opportunities = tuple(OpportunitySnapshot.model_validate_json(line) for line in lines if line)
        ids = tuple(item.opportunity_id for item in opportunities)
        matches = tuple(item for item in opportunities if item.opportunity_id == opportunity_id)
        if len(ids) != len(set(ids)) or len(matches) != 1:
            raise InvalidKrThemeDayOpportunitySourceError
        return matches[0]
    except (OSError, TypeError, UnicodeError, ValidationError, ValueError):
        raise InvalidKrThemeDayOpportunitySourceError from None


def _require_private_file(path: Path) -> None:
    if path.is_symlink():
        raise InvalidKrThemeDayOpportunitySourceError
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidKrThemeDayOpportunitySourceError

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import override

from pydantic import ValidationError

from trading_agent.private_immutable_file import read_private_text
from trading_agent.signal_contract_models import OpportunitySnapshot


class InvalidKrThemeDayOpportunitySourceError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day opportunity source is invalid"


def load_exact_kr_theme_opportunity(path: Path, opportunity_id: str) -> OpportunitySnapshot:
    try:
        lines = read_private_text(path).splitlines()
        opportunities = tuple(OpportunitySnapshot.model_validate_json(line) for line in lines if line)
        ids = tuple(item.opportunity_id for item in opportunities)
        matches = tuple(item for item in opportunities if item.opportunity_id == opportunity_id)
        if len(ids) != len(set(ids)) or len(matches) != 1:
            raise InvalidKrThemeDayOpportunitySourceError
        return matches[0]
    except (OSError, TypeError, UnicodeError, ValidationError, ValueError):
        raise InvalidKrThemeDayOpportunitySourceError from None


def kr_theme_day_opportunity_sha256(opportunity: OpportunitySnapshot) -> str:
    try:
        payload = json.dumps(
            opportunity.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()
    except (AttributeError, TypeError, ValueError):
        raise InvalidKrThemeDayOpportunitySourceError from None

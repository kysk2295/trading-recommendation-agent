from __future__ import annotations

import datetime as dt
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.signal_contract_models import TradeSignalEnvelope

MAX_PUBLICATION_AGE: Final = dt.timedelta(minutes=5)


class TradeSignalPublication(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    published_at: dt.datetime
    signal: TradeSignalEnvelope

    @model_validator(mode="after")
    def validate_publication(self) -> Self:
        if (
            not _aware(self.published_at)
            or self.published_at < self.signal.observed_at
            or self.published_at >= self.signal.valid_until
            or self.published_at - self.signal.observed_at > MAX_PUBLICATION_AGE
        ):
            raise ValueError("invalid trade signal publication")
        return self


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None

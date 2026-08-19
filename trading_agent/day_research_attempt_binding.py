from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_types import aware

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ARTIFACT_REF = re.compile(r"^artifact://safe/[0-9a-f]{64}$")


class InvalidDayResearchAttemptBindingError(ValueError):
    pass


class DayResearchAttemptBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always", strict=True)

    binding_id: str
    attempt_id: str = Field(min_length=1)
    market_id: MarketId
    hypothesis_version_id: str
    artifact_ref: str
    multiple_testing_family: str
    search_budget_debit: int = Field(ge=1)
    bound_at: dt.datetime

    @field_validator("bound_at")
    @classmethod
    def normalize_bound_at(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC) if aware(value) else value

    @classmethod
    def canonical_id_for(cls, payload: Mapping[str, object]) -> str:
        normalized = {key: value for key, value in payload.items() if key != "binding_id"}
        encoded = json.dumps(
            cls._canonical_value(normalized), ensure_ascii=True, separators=(",", ":"), sort_keys=True
        )
        return hashlib.sha256(encoded.encode()).hexdigest()

    @classmethod
    def _canonical_value(cls, value: object) -> object:
        match value:
            case Mapping() as mapping:
                return {str(key): cls._canonical_value(item) for key, item in mapping.items()}
            case dt.datetime() as timestamp:
                if not aware(timestamp):
                    return timestamp.isoformat()
                return timestamp.astimezone(dt.UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
            case MarketId() as market_id:
                return market_id.value
            case None | bool() | int() | float() | str():
                return value
            case unsupported:
                raise TypeError(f"unsupported binding identity value: {type(unsupported).__name__}")

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        payload = self.model_dump(mode="python")
        if update is not None:
            payload.update(update)
        return self.__class__.model_validate(payload)

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if (
            _HEX64.fullmatch(self.binding_id) is None
            or _HEX64.fullmatch(self.hypothesis_version_id) is None
            or not _canonical_text(self.attempt_id)
            or _SAFE_ARTIFACT_REF.fullmatch(self.artifact_ref) is None
            or not _canonical_text(self.multiple_testing_family)
            or not aware(self.bound_at)
            or self.binding_id != self.canonical_id_for(self.model_dump(mode="python"))
        ):
            raise InvalidDayResearchAttemptBindingError("invalid_day_research_attempt_binding")
        return self


def preregistered_attempted_artifact_ref(code_sha256: str) -> str:
    if _HEX64.fullmatch(code_sha256) is None:
        raise InvalidDayResearchAttemptBindingError("invalid_attempt_code_identity")
    return f"artifact://safe/{code_sha256}"


def is_safe_artifact_ref(value: str) -> bool:
    return _SAFE_ARTIFACT_REF.fullmatch(value) is not None


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip()


__all__ = (
    "DayResearchAttemptBinding",
    "InvalidDayResearchAttemptBindingError",
    "is_safe_artifact_ref",
    "preregistered_attempted_artifact_ref",
)

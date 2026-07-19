from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.kr_theme_keyword import KrKeywordRuleSet

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class KrThemeProjectionManifestError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme projection run manifest 또는 rules가 유효하지 않습니다"


class KrThemeProjectionRunManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    collection_cycle_id: str
    rules_path: str
    classification_run_id: str
    classified_at: dt.datetime
    projected_at: dt.datetime
    validity_seconds: int
    producer_strategy_version: str
    runtime_code_version: str | None = None

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        rules_path = Path(self.rules_path)
        if (
            _SAFE_ID.fullmatch(self.collection_cycle_id) is None
            or rules_path.is_absolute()
            or not rules_path.parts
            or any(part in {".", ".."} for part in rules_path.parts)
            or _SAFE_ID.fullmatch(self.classification_run_id) is None
            or not _aware(self.classified_at)
            or not _aware(self.projected_at)
            or self.projected_at < self.classified_at
            or not 1 <= self.validity_seconds <= 3600
            or _SAFE_ID.fullmatch(self.producer_strategy_version) is None
            or (self.runtime_code_version is not None and _SAFE_ID.fullmatch(self.runtime_code_version) is None)
        ):
            raise ValueError("invalid KR theme projection run")
        return self


@dataclass(frozen=True, slots=True)
class LoadedKrThemeProjectionRun:
    run: KrThemeProjectionRunManifest
    rules: KrKeywordRuleSet


def load_kr_theme_projection_run(path: Path) -> LoadedKrThemeProjectionRun:
    try:
        manifest_path = path.resolve(strict=True)
        if not manifest_path.is_file():
            raise OSError
        run = KrThemeProjectionRunManifest.model_validate_json(manifest_path.read_bytes())
        base = manifest_path.parent
        rules_path = (base / run.rules_path).resolve(strict=True)
        if not rules_path.is_relative_to(base) or not rules_path.is_file():
            raise KrThemeProjectionManifestError
        rules = KrKeywordRuleSet.model_validate_json(rules_path.read_bytes())
        return LoadedKrThemeProjectionRun(run=run, rules=rules)
    except KrThemeProjectionManifestError:
        raise
    except (OSError, UnicodeError, ValidationError, ValueError):
        raise KrThemeProjectionManifestError from None


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None

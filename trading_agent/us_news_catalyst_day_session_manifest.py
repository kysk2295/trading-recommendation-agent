from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.us_equity_calendar import regular_session_bounds
from trading_agent.us_news_catalyst_trial_contract import us_news_catalyst_trial_id


class InvalidUsNewsCatalystDaySessionManifestError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst day session manifest is invalid"


class UsNewsCatalystDaySessionPaths(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_ledger: Path
    registration_manifest: Path
    projection_root: Path
    evidence_root: Path
    security_master_store: Path
    artifact_root: Path
    plan_root: Path
    profile_root: Path
    runtime_root: Path
    canonical_root: Path
    feature_root: Path
    receipt_root: Path
    review_root: Path
    audit_store: Path
    output_root: Path
    secret_path: Path

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        values = tuple(value for _name, value in self)
        if any(not value.is_absolute() for value in values) or len(set(values)) != len(values):
            raise InvalidUsNewsCatalystDaySessionManifestError
        return self


class UsNewsCatalystDaySessionIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_version: str
    code_version: str
    session_date: dt.date
    created_at: dt.datetime
    paths: UsNewsCatalystDaySessionPaths

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if (
            not _canonical_text(self.strategy_version)
            or not _canonical_text(self.code_version)
            or not _aware(self.created_at)
            or regular_session_bounds(self.session_date) is None
        ):
            raise InvalidUsNewsCatalystDaySessionManifestError
        return self


class UsNewsCatalystDaySessionManifest(UsNewsCatalystDaySessionIdentity):
    schema_version: Literal[1] = 1
    session_id: str
    trial_id: str

    @model_validator(mode="after")
    def validate_ids(self) -> Self:
        if (
            self.trial_id != us_news_catalyst_trial_id(self.strategy_version, self.session_date)
            or self.session_id != _session_id(self)
        ):
            raise InvalidUsNewsCatalystDaySessionManifestError
        return self


def build_us_news_catalyst_day_session_manifest(
    identity: UsNewsCatalystDaySessionIdentity,
) -> UsNewsCatalystDaySessionManifest:
    checked = UsNewsCatalystDaySessionIdentity.model_validate(identity.model_dump(mode="python"))
    trial_id = us_news_catalyst_trial_id(checked.strategy_version, checked.session_date)
    provisional = UsNewsCatalystDaySessionManifest.model_construct(
        schema_version=1,
        session_id="0" * 64,
        trial_id=trial_id,
        strategy_version=checked.strategy_version,
        code_version=checked.code_version,
        session_date=checked.session_date,
        created_at=checked.created_at,
        paths=checked.paths,
    )
    return UsNewsCatalystDaySessionManifest.model_validate(
        {**provisional.model_dump(mode="python"), "session_id": _session_id(provisional)}
    )


def write_us_news_catalyst_day_session_manifest(
    path: Path,
    manifest: UsNewsCatalystDaySessionManifest,
) -> bool:
    try:
        checked = UsNewsCatalystDaySessionManifest.model_validate(manifest.model_dump(mode="python"))
        target = path.expanduser().absolute()
        created = publish_private_immutable_text(target, _canonical(checked) + "\n")
        if not created and load_us_news_catalyst_day_session_manifest(target) != checked:
            raise InvalidUsNewsCatalystDaySessionManifestError
        return created
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidUsNewsCatalystDaySessionManifestError from None


def load_us_news_catalyst_day_session_manifest(path: Path) -> UsNewsCatalystDaySessionManifest:
    try:
        payload = read_private_text(path.expanduser().absolute())
        manifest = UsNewsCatalystDaySessionManifest.model_validate_json(payload)
        if payload != _canonical(manifest) + "\n":
            raise InvalidUsNewsCatalystDaySessionManifestError
        return manifest
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidUsNewsCatalystDaySessionManifestError from None


def _session_id(manifest: UsNewsCatalystDaySessionManifest) -> str:
    payload = manifest.model_dump(mode="json", exclude={"session_id"})
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _canonical(manifest: UsNewsCatalystDaySessionManifest) -> str:
    return json.dumps(
        manifest.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip() and not any(char in value for char in "\r\n\t")


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "InvalidUsNewsCatalystDaySessionManifestError",
    "UsNewsCatalystDaySessionIdentity",
    "UsNewsCatalystDaySessionManifest",
    "UsNewsCatalystDaySessionPaths",
    "build_us_news_catalyst_day_session_manifest",
    "load_us_news_catalyst_day_session_manifest",
    "write_us_news_catalyst_day_session_manifest",
)

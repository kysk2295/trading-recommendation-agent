from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.kr_instrument import is_kr_instrument_symbol_v2
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.private_query_file import read_private_text_query_only

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class InvalidKrThemeDaySessionManifestError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day session manifest is invalid"


class KrThemeDaySessionPaths(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_ledger: Path
    calendar_store: Path
    opportunity_outbox: Path
    receipt_store: Path
    entry_store: Path
    exit_store: Path
    terminal_store: Path
    review_store: Path
    audit_store: Path
    output_root: Path
    intraday_fixture_manifest: Path | None = None
    eod_fixture_manifest: Path | None = None

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        required = tuple(
            value for name, value in self if name not in {"intraday_fixture_manifest", "eod_fixture_manifest"}
        )
        fixtures = (self.intraday_fixture_manifest, self.eod_fixture_manifest)
        if (
            any(not path.is_absolute() for path in required)
            or len(set(required)) != len(required)
            or (fixtures[0] is None) != (fixtures[1] is None)
            or any(path is not None and not path.is_absolute() for path in fixtures)
        ):
            raise InvalidKrThemeDaySessionManifestError
        return self


class KrThemeDaySessionIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_version: str
    code_version: str
    session_date: dt.date
    registered_at: dt.datetime
    onboarded_at: dt.datetime
    calendar_snapshot_id: str
    opportunity_id: str
    opportunity_strategy_version: str
    opportunity_sha256: str
    symbol: str
    paths: KrThemeDaySessionPaths

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        local = self.registered_at.astimezone(dt.timezone(dt.timedelta(hours=9)))
        onboarded_local = self.onboarded_at.astimezone(dt.timezone(dt.timedelta(hours=9)))
        values = (
            self.strategy_version,
            self.code_version,
            self.opportunity_id,
            self.opportunity_strategy_version,
        )
        if (
            not _aware(self.registered_at)
            or not _aware(self.onboarded_at)
            or self.registered_at >= self.onboarded_at
            or local.date() > self.session_date
            or (self.session_date - local.date()).days > 7
            or local.time() >= dt.time(9)
            or onboarded_local.date() != self.session_date
            or not dt.time(9) <= onboarded_local.time() < dt.time(15, 30)
            or any(not value or value != value.strip() for value in values)
            or _HEX64.fullmatch(self.calendar_snapshot_id) is None
            or _HEX64.fullmatch(self.opportunity_sha256) is None
            or not is_kr_instrument_symbol_v2(self.symbol)
        ):
            raise InvalidKrThemeDaySessionManifestError
        return self


class KrThemeDaySessionManifest(KrThemeDaySessionIdentity):
    schema_version: Literal[2] = 2
    session_id: str

    @model_validator(mode="after")
    def validate_session_id(self) -> Self:
        if self.session_id != _session_id(self):
            raise InvalidKrThemeDaySessionManifestError
        return self


def build_kr_theme_day_session_manifest(identity: KrThemeDaySessionIdentity) -> KrThemeDaySessionManifest:
    validated = KrThemeDaySessionIdentity.model_validate(identity.model_dump(mode="python"))
    provisional = KrThemeDaySessionManifest.model_construct(
        schema_version=2,
        session_id="0" * 64,
        strategy_version=validated.strategy_version,
        code_version=validated.code_version,
        session_date=validated.session_date,
        registered_at=validated.registered_at,
        onboarded_at=validated.onboarded_at,
        calendar_snapshot_id=validated.calendar_snapshot_id,
        opportunity_id=validated.opportunity_id,
        opportunity_strategy_version=validated.opportunity_strategy_version,
        opportunity_sha256=validated.opportunity_sha256,
        symbol=validated.symbol,
        paths=validated.paths,
    )
    return KrThemeDaySessionManifest.model_validate(
        {**provisional.model_dump(mode="python"), "session_id": _session_id(provisional)}
    )


def write_kr_theme_day_session_manifest(path: Path, manifest: KrThemeDaySessionManifest) -> bool:
    try:
        validated = KrThemeDaySessionManifest.model_validate(manifest.model_dump(mode="python"))
        target = path.expanduser().absolute()
        created = publish_private_immutable_text(target, _canonical(validated) + "\n")
        if not created and load_kr_theme_day_session_manifest(target) != validated:
            raise InvalidKrThemeDaySessionManifestError
        return created
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeDaySessionManifestError from None


def load_kr_theme_day_session_manifest(path: Path) -> KrThemeDaySessionManifest:
    try:
        target = path.expanduser().absolute()
        return _parse_manifest(read_private_text(target))
    except (OSError, TypeError, UnicodeError, ValidationError, ValueError):
        raise InvalidKrThemeDaySessionManifestError from None


def load_kr_theme_day_session_manifest_query_only(path: Path) -> KrThemeDaySessionManifest:
    try:
        target = path.expanduser().absolute()
        return _parse_manifest(read_private_text_query_only(target))
    except (OSError, TypeError, UnicodeError, ValidationError, ValueError):
        raise InvalidKrThemeDaySessionManifestError from None


def _parse_manifest(payload: str) -> KrThemeDaySessionManifest:
    manifest = KrThemeDaySessionManifest.model_validate_json(payload)
    if payload != _canonical(manifest) + "\n":
        raise InvalidKrThemeDaySessionManifestError
    return manifest


def _session_id(manifest: KrThemeDaySessionManifest) -> str:
    payload = manifest.model_dump(mode="json", exclude={"session_id"})
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _canonical(manifest: KrThemeDaySessionManifest) -> str:
    return json.dumps(manifest.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None

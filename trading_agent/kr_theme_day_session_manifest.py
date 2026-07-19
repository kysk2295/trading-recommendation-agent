from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.kr_instrument import is_kr_instrument_symbol_v2
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)

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
    calendar_snapshot_id: str
    opportunity_id: str
    opportunity_strategy_version: str
    opportunity_sha256: str
    symbol: str
    paths: KrThemeDaySessionPaths

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        local = self.registered_at.astimezone(dt.timezone(dt.timedelta(hours=9)))
        values = (
            self.strategy_version,
            self.code_version,
            self.opportunity_id,
            self.opportunity_strategy_version,
        )
        if (
            not _aware(self.registered_at)
            or local.date() > self.session_date
            or (self.session_date - local.date()).days > 7
            or local.time() >= dt.time(9)
            or any(not value or value != value.strip() for value in values)
            or _HEX64.fullmatch(self.calendar_snapshot_id) is None
            or _HEX64.fullmatch(self.opportunity_sha256) is None
            or not is_kr_instrument_symbol_v2(self.symbol)
        ):
            raise InvalidKrThemeDaySessionManifestError
        return self


class KrThemeDaySessionManifest(KrThemeDaySessionIdentity):
    schema_version: Literal[1] = 1
    session_id: str

    @model_validator(mode="after")
    def validate_session_id(self) -> Self:
        if self.session_id != _session_id(self):
            raise InvalidKrThemeDaySessionManifestError
        return self


def build_kr_theme_day_session_manifest(identity: KrThemeDaySessionIdentity) -> KrThemeDaySessionManifest:
    validated = KrThemeDaySessionIdentity.model_validate(identity.model_dump(mode="python"))
    provisional = KrThemeDaySessionManifest.model_construct(
        schema_version=1,
        session_id="0" * 64,
        strategy_version=validated.strategy_version,
        code_version=validated.code_version,
        session_date=validated.session_date,
        registered_at=validated.registered_at,
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


def write_kr_theme_day_session_manifest(path: Path, manifest: KrThemeDaySessionManifest) -> None:
    try:
        validated = KrThemeDaySessionManifest.model_validate(manifest.model_dump(mode="python"))
        target = path.expanduser().absolute()
        if target.is_symlink() or target.exists():
            raise InvalidKrThemeDaySessionManifestError
        if not publish_private_immutable_text(target, _canonical(validated) + "\n"):
            raise InvalidKrThemeDaySessionManifestError
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeDaySessionManifestError from None


def load_kr_theme_day_session_manifest(path: Path) -> KrThemeDaySessionManifest:
    try:
        target = path.expanduser().absolute()
        _require_private(target)
        manifest = KrThemeDaySessionManifest.model_validate_json(target.read_text(encoding="utf-8"))
        if target.read_text(encoding="utf-8") != _canonical(manifest) + "\n":
            raise InvalidKrThemeDaySessionManifestError
        return manifest
    except (OSError, TypeError, UnicodeError, ValidationError, ValueError):
        raise InvalidKrThemeDaySessionManifestError from None


def _session_id(manifest: KrThemeDaySessionManifest) -> str:
    payload = manifest.model_dump(mode="json", exclude={"session_id"})
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _canonical(manifest: KrThemeDaySessionManifest) -> str:
    return json.dumps(manifest.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _require_private(path: Path) -> None:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidKrThemeDaySessionManifestError


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None

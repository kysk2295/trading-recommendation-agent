from __future__ import annotations

import datetime as dt
import os
import stat
from pathlib import Path
from typing import Final, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

SYSTEM_CURRENT_AUTHORITY_FILE: Final = "system-current-authority.v1.json"
SYSTEM_CURRENT_AUTHORITY_ROOT: Final = "source_evidence"


class SystemCurrentAuthority(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1]
    evidence_type: Literal["system_current_authority"]
    observed_at: AwareDatetime
    railway_deployment_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    railway_code_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    railway_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    railway_source_root_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    relay_transition_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    relay_owner_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    relay_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    relay_source_root_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def read_system_current_authority(
    path: Path,
    now: dt.datetime,
) -> SystemCurrentAuthority | str:
    if not path.exists():
        return "system_current_authority_missing"
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            return "system_current_authority_permissions_invalid"
        payload = path.read_bytes()
        if not payload or len(payload) > 16 * 1024:
            return "system_current_authority_invalid"
        authority = SystemCurrentAuthority.model_validate_json(payload)
    except (OSError, ValidationError, ValueError):
        return "system_current_authority_invalid"
    if authority.observed_at > now + dt.timedelta(minutes=5):
        return "system_current_authority_future"
    if now - authority.observed_at > dt.timedelta(minutes=5):
        return "system_current_authority_stale"
    return authority


__all__ = (
    "SYSTEM_CURRENT_AUTHORITY_FILE",
    "SYSTEM_CURRENT_AUTHORITY_ROOT",
    "SystemCurrentAuthority",
    "read_system_current_authority",
)

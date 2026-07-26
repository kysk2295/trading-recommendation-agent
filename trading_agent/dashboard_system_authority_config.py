from __future__ import annotations

import base64
import os
import stat
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trading_agent.dashboard_system_current_authority import (
    SystemAuthorityVerifier,
    SystemAuthorityVerifierInput,
    UnavailableSystemAuthorityVerifier,
)

MAX_SYSTEM_AUTHORITY_CONFIG_BYTES: Final = 4_096


class SystemAuthorityPublicConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    key_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    project_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    environment: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    railway_service_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    relay_service_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    ed25519_public_key_base64: str = Field(
        pattern=r"^[A-Za-z0-9+/]{43}=$",
    )


def load_system_authority_verifier(
    path: Path,
    *,
    untrusted_root: Path | None = None,
) -> SystemAuthorityVerifierInput:
    if not path.is_absolute() or (
        untrusted_root is not None
        and (
            path.absolute().is_relative_to(untrusted_root.absolute())
            or path.resolve(strict=False).is_relative_to(
                untrusted_root.resolve(strict=False)
            )
        )
    ):
        return UnavailableSystemAuthorityVerifier(
            "system_current_authority_verifier_invalid"
        )
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
    except FileNotFoundError:
        return UnavailableSystemAuthorityVerifier(
            "system_current_authority_verifier_missing"
        )
    except OSError:
        return UnavailableSystemAuthorityVerifier(
            "system_current_authority_verifier_invalid"
        )
    try:
        metadata = os.fstat(descriptor)
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or mode not in {0o600, 0o644}
            or metadata.st_nlink != 1
        ):
            raise ValueError
        payload = os.read(descriptor, MAX_SYSTEM_AUTHORITY_CONFIG_BYTES + 1)
        if not payload or len(payload) > MAX_SYSTEM_AUTHORITY_CONFIG_BYTES:
            raise ValueError
        config = SystemAuthorityPublicConfig.model_validate_json(payload)
        public_key = base64.b64decode(
            config.ed25519_public_key_base64,
            validate=True,
        )
        return SystemAuthorityVerifier.from_public_bytes(
            key_id=config.key_id,
            project_id=config.project_id,
            environment=config.environment,
            railway_service_id=config.railway_service_id,
            relay_service_id=config.relay_service_id,
            public_key=public_key,
        )
    except (OSError, ValidationError, ValueError):
        return UnavailableSystemAuthorityVerifier(
            "system_current_authority_verifier_invalid"
        )
    finally:
        os.close(descriptor)


__all__ = (
    "MAX_SYSTEM_AUTHORITY_CONFIG_BYTES",
    "SystemAuthorityPublicConfig",
    "load_system_authority_verifier",
)

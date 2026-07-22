from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Final, override

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_private_directory_query_only,
)

_MAX_SECRET_BYTES: Final = 4_096


class PrivateAlpacaCredentialsError(PermissionError):
    @override
    def __str__(self) -> str:
        return "private Alpaca credentials are invalid"


def load_private_alpaca_credentials(path: Path) -> AlpacaCredentials:
    try:
        absolute = absolute_private_path(path)
        parent = open_private_parent(absolute.parent, create=False)
        try:
            require_private_directory_query_only(parent)
            descriptor = os.open(
                absolute.name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
            try:
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.getuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                    or metadata.st_nlink != 1
                ):
                    raise PrivateAlpacaCredentialsError
                payload = bytearray()
                while len(payload) <= _MAX_SECRET_BYTES:
                    chunk = os.read(descriptor, _MAX_SECRET_BYTES + 1 - len(payload))
                    if not chunk:
                        break
                    payload.extend(chunk)
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)
        if len(payload) > _MAX_SECRET_BYTES:
            raise PrivateAlpacaCredentialsError
        values: dict[str, str] = {}
        for raw_line in bytes(payload).decode("utf-8").splitlines():
            name, separator, value = raw_line.partition("=")
            if separator:
                values[name] = value.strip()
        key_id = values.get("APCA_API_KEY_ID", "")
        secret_key = values.get("APCA_API_SECRET_KEY", "")
        if not key_id or not secret_key:
            raise PrivateAlpacaCredentialsError
        return AlpacaCredentials(key_id=key_id, secret_key=secret_key)
    except (OSError, UnicodeError, ValueError):
        raise PrivateAlpacaCredentialsError from None


__all__ = (
    "PrivateAlpacaCredentialsError",
    "load_private_alpaca_credentials",
)

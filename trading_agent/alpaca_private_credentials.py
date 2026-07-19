from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import override

from trading_agent.alpaca_http import AlpacaCredentials, load_alpaca_credentials


class PrivateAlpacaCredentialsError(PermissionError):
    @override
    def __str__(self) -> str:
        return "private Alpaca credentials are invalid"


def load_private_alpaca_credentials(path: Path) -> AlpacaCredentials:
    try:
        metadata = path.expanduser().absolute().lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise PrivateAlpacaCredentialsError
        return load_alpaca_credentials(path.expanduser().absolute())
    except (OSError, ValueError):
        raise PrivateAlpacaCredentialsError from None


__all__ = (
    "PrivateAlpacaCredentialsError",
    "load_private_alpaca_credentials",
)

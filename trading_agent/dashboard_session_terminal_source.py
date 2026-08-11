from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

from trading_agent.hermes_delivery_errors import InvalidHermesDeliveryStoreError
from trading_agent.hermes_delivery_models import HermesDeliveryEvent
from trading_agent.hermes_delivery_reader import HermesDeliveryReader


class InvalidDashboardSessionTerminalSourceError(ValueError):
    pass


def read_private_session_terminal_events(path: Path) -> tuple[HermesDeliveryEvent, ...]:
    try:
        parent = path.parent.lstat()
        metadata = path.lstat()
        if (
            path.parent.is_symlink()
            or path.is_symlink()
            or not stat.S_ISDIR(parent.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or parent.st_uid != os.getuid()
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) != 0o700
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise InvalidDashboardSessionTerminalSourceError
        return HermesDeliveryReader(path).events()
    except InvalidDashboardSessionTerminalSourceError:
        raise
    except (InvalidHermesDeliveryStoreError, OSError, sqlite3.Error):
        raise InvalidDashboardSessionTerminalSourceError from None


__all__ = (
    "InvalidDashboardSessionTerminalSourceError",
    "read_private_session_terminal_events",
)

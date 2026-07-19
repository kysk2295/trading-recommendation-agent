from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import override

from pydantic import ValidationError

from trading_agent.trade_signal_publication import TradeSignalPublication


class TradeSignalOutboxReaderError(ValueError):
    @override
    def __str__(self) -> str:
        return "trade signal outbox is invalid"


def read_trade_signal_publications(
    path: Path,
) -> tuple[TradeSignalPublication, ...]:
    source = path.expanduser().absolute()
    if source.is_symlink():
        raise TradeSignalOutboxReaderError
    if not source.exists():
        return ()
    try:
        metadata = source.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
        ):
            raise TradeSignalOutboxReaderError
        lines = source.read_bytes().splitlines()
        publications = tuple(TradeSignalPublication.model_validate_json(line) for line in lines)
        signal_ids = tuple(item.signal.signal_id for item in publications)
        if not lines or len(signal_ids) != len(set(signal_ids)):
            raise TradeSignalOutboxReaderError
        return publications
    except (OSError, TypeError, ValidationError, ValueError):
        raise TradeSignalOutboxReaderError from None


__all__ = (
    "TradeSignalOutboxReaderError",
    "read_trade_signal_publications",
)

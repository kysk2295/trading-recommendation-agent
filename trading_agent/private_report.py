from __future__ import annotations

import os
import tempfile
from pathlib import Path

PRIVATE_REPORT_MODE = 0o600


def write_private_report(destination: Path, content: str) -> None:
    """Atomically replace an operational report with owner-only permissions."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".writing",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            _ = handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.chmod(PRIVATE_REPORT_MODE)
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

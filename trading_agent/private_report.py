from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from io import TextIOWrapper
from pathlib import Path

PRIVATE_REPORT_MODE = 0o600


@contextmanager
def open_private_append(destination: Path) -> Iterator[TextIOWrapper]:
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
        PRIVATE_REPORT_MODE,
    )
    try:
        os.fchmod(descriptor, PRIVATE_REPORT_MODE)
        handle = os.fdopen(
            descriptor,
            "a",
            encoding="utf-8",
            newline="",
        )
    except OSError:
        os.close(descriptor)
        raise
    with handle:
        yield handle


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

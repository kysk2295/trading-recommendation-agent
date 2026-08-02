from __future__ import annotations

import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class ResearchAgentRuntimeLeaseUnavailableError(RuntimeError):
    pass


@contextmanager
def research_agent_runtime_lease(path: Path) -> Iterator[None]:
    if not path.is_absolute() or path.is_symlink():
        raise ResearchAgentRuntimeLeaseUnavailableError
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _require_private_regular_file(path)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ResearchAgentRuntimeLeaseUnavailableError from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _require_private_regular_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise ResearchAgentRuntimeLeaseUnavailableError


__all__ = (
    "ResearchAgentRuntimeLeaseUnavailableError",
    "research_agent_runtime_lease",
)

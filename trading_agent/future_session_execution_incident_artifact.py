from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Final

_COMMIT: Final = re.compile(r"^[0-9a-f]{40}$")
_PUBLISHER_SOURCE: Final = "run_future_session_execution_incident_publisher.py"
_MAX_SOURCE_BYTES: Final = 256 * 1024


class InvalidExecutionIncidentPublisherArtifactError(ValueError):
    pass


def read_execution_incident_publisher_at_commit(repository: Path, commit: str) -> bytes:
    if not repository.is_absolute() or _COMMIT.fullmatch(commit) is None:
        raise InvalidExecutionIncidentPublisherArtifactError
    try:
        completed = subprocess.run(
            ("git", "-C", str(repository), "show", f"{commit}:{_PUBLISHER_SOURCE}"),
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        raise InvalidExecutionIncidentPublisherArtifactError from None
    if (
        completed.returncode != 0
        or completed.stderr
        or not completed.stdout
        or len(completed.stdout) > _MAX_SOURCE_BYTES
    ):
        raise InvalidExecutionIncidentPublisherArtifactError
    return completed.stdout


__all__ = (
    "InvalidExecutionIncidentPublisherArtifactError",
    "read_execution_incident_publisher_at_commit",
)

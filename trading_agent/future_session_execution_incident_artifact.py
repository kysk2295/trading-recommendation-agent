from __future__ import annotations

import re
import stat
import subprocess
from pathlib import Path
from typing import Final

_COMMIT: Final = re.compile(r"^[0-9a-f]{40}$")
_PUBLISHER_SOURCE: Final = "run_future_session_execution_incident_publisher.py"
_MAX_SOURCE_BYTES: Final = 256 * 1024


class InvalidExecutionIncidentPublisherArtifactError(ValueError):
    pass


def read_execution_incident_publisher_at_commit(repository: Path, commit: str) -> bytes:
    try:
        git_directory = (repository / ".git").lstat()
        repository_owner = repository.stat().st_uid
    except OSError:
        raise InvalidExecutionIncidentPublisherArtifactError from None
    if (
        not repository.is_absolute()
        or _COMMIT.fullmatch(commit) is None
        or not stat.S_ISDIR(git_directory.st_mode)
        or git_directory.st_uid != repository_owner
    ):
        raise InvalidExecutionIncidentPublisherArtifactError
    try:
        completed = subprocess.run(
            (
                "/usr/bin/git",
                "--no-replace-objects",
                "-C",
                str(repository),
                "show",
                f"{commit}:{_PUBLISHER_SOURCE}",
            ),
            check=False,
            capture_output=True,
            env={
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
            },
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

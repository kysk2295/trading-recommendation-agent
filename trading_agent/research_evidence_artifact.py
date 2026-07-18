from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import override

from pydantic import ValidationError

from trading_agent.research_evidence_models import (
    ResearchEvidenceReadModel,
    content_sha256,
)


class ResearchEvidenceArtifactError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "research evidence artifact is invalid"


def write_research_evidence_artifact(
    root: Path,
    model: ResearchEvidenceReadModel,
) -> tuple[Path, bool]:
    destination: Path | None = None
    created = False
    try:
        checked = ResearchEvidenceReadModel.model_validate(model.model_dump(mode="python"))
        content = _model_bytes(checked)
        artifact_root = _private_root(root)
        destination = artifact_root / f"research_evidence_{checked.content_sha256}.json"
        if destination.exists() or destination.is_symlink():
            if _private_file(destination) and destination.read_bytes() == content:
                _ = load_research_evidence_artifact(destination)
                return destination, False
            raise ResearchEvidenceArtifactError
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        try:
            _write_all(descriptor, content)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory_fd = os.open(artifact_root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return destination, True
    except ResearchEvidenceArtifactError:
        raise
    except (OSError, TypeError, ValidationError, ValueError):
        if created and destination is not None:
            destination.unlink(missing_ok=True)
        raise ResearchEvidenceArtifactError from None


def load_research_evidence_artifact(path: Path) -> ResearchEvidenceReadModel:
    try:
        candidate = path.expanduser().absolute()
        if not _private_file(candidate):
            raise OSError
        model = ResearchEvidenceReadModel.model_validate_json(candidate.read_bytes())
        payload = model.model_dump(mode="json")
        digest = payload.pop("content_sha256")
        if (
            digest != content_sha256(payload)
            or candidate.name != f"research_evidence_{digest}.json"
            or candidate.read_bytes() != _model_bytes(model)
        ):
            raise ValueError
        return model
    except (OSError, UnicodeError, ValidationError, ValueError):
        raise ResearchEvidenceArtifactError from None


def _private_root(root: Path) -> Path:
    candidate = root.expanduser().absolute()
    if candidate.is_symlink():
        raise ResearchEvidenceArtifactError
    if not candidate.exists():
        candidate.mkdir(parents=True, mode=0o700)
        candidate.chmod(0o700)
    metadata = candidate.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise ResearchEvidenceArtifactError
    return candidate


def _private_file(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    metadata = path.lstat()
    return stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.getuid() and stat.S_IMODE(metadata.st_mode) == 0o600


def _model_bytes(model: ResearchEvidenceReadModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        + b"\n"
    )


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError
        offset += written


__all__ = (
    "ResearchEvidenceArtifactError",
    "load_research_evidence_artifact",
    "write_research_evidence_artifact",
)

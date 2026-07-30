from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path


def write_private_file(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode,
    )
    try:
        os.fchmod(descriptor, mode)
        written = os.write(descriptor, content)
        if written != len(content):
            raise OSError("short write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def stage_path(stage: Path, output_dir: Path, final: Path) -> Path:
    return stage / final.relative_to(output_dir)


__all__ = ("sha256", "stage_path", "write_private_file")

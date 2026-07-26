from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Final

_FORBIDDEN_EXECUTABLES: Final = frozenset({Path("/bin/sh"), Path("/usr/bin/env")})


@dataclass(frozen=True, slots=True)
class InvalidExecutableBindingError(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class ExecutableIdentity:
    path: Path
    device: int
    inode: int
    owner: int
    mode: int
    size: int
    sha256: str
    interpreter: Path | None


def capture_executable(path: Path) -> ExecutableIdentity:
    if path.is_symlink():
        raise InvalidExecutableBindingError("executable_symlink_forbidden")
    if ".." in path.parts:
        raise InvalidExecutableBindingError("executable_path_traversal")
    try:
        normalized = path.resolve(strict=True)
        descriptor = os.open(normalized, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise InvalidExecutableBindingError("executable_unavailable") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise InvalidExecutableBindingError("executable_not_regular")
        if metadata.st_nlink != 1:
            raise InvalidExecutableBindingError("executable_hardlink_forbidden")
        if metadata.st_uid not in {0, os.geteuid()}:
            raise InvalidExecutableBindingError("executable_owner_forbidden")
        if metadata.st_mode & 0o022:
            raise InvalidExecutableBindingError("executable_writable_by_others")
        if not metadata.st_mode & 0o111:
            raise InvalidExecutableBindingError("executable_not_executable")
        digest = _descriptor_sha256(descriptor)
        interpreter = _interpreter_path(descriptor)
    finally:
        os.close(descriptor)
    if normalized in _FORBIDDEN_EXECUTABLES:
        raise InvalidExecutableBindingError("executable_forbidden")
    if interpreter is not None:
        _ = capture_interpreter(interpreter)
    return ExecutableIdentity(
        path=normalized,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        owner=metadata.st_uid,
        mode=stat.S_IMODE(metadata.st_mode),
        size=metadata.st_size,
        sha256=digest,
        interpreter=interpreter,
    )


def capture_interpreter(path: Path) -> ExecutableIdentity:
    if path in _FORBIDDEN_EXECUTABLES:
        raise InvalidExecutableBindingError("executable_interpreter_forbidden")
    identity = capture_executable(path)
    if identity.interpreter is not None:
        raise InvalidExecutableBindingError("nested_executable_interpreter_forbidden")
    return identity


def _descriptor_sha256(descriptor: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = sha256()
    while chunk := os.read(descriptor, 64 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _interpreter_path(descriptor: int) -> Path | None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    first_line = os.read(descriptor, 4 * 1024).splitlines()[:1]
    if not first_line or not first_line[0].startswith(b"#!"):
        return None
    try:
        shebang = first_line[0][2:].decode("utf-8", errors="strict").strip().split()
        if len(shebang) != 1 or not Path(shebang[0]).is_absolute():
            raise InvalidExecutableBindingError("executable_interpreter_forbidden")
        return Path(shebang[0]).resolve(strict=True)
    except (OSError, UnicodeDecodeError) as error:
        raise InvalidExecutableBindingError("executable_interpreter_forbidden") from error


__all__ = (
    "ExecutableIdentity",
    "InvalidExecutableBindingError",
    "capture_executable",
    "capture_interpreter",
)

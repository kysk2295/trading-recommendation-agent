from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

_SHELL_NAMES = frozenset({"ash", "bash", "csh", "dash", "env", "fish", "ksh", "sh", "tcsh", "zsh"})


class InvalidExecutableBindingError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

@dataclass(frozen=True, slots=True)
class FileIdentity:
    path: Path
    device: int
    inode: int
    owner: int
    mode: int
    size: int
    sha256: str


def capture_file(path: Path, *, executable: bool) -> FileIdentity:
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
        if executable and not metadata.st_mode & 0o111:
            raise InvalidExecutableBindingError("executable_not_executable")
        digest = _descriptor_sha256(descriptor)
    finally:
        os.close(descriptor)
    return FileIdentity(
        normalized,
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        stat.S_IMODE(metadata.st_mode),
        metadata.st_size,
        digest,
    )


def capture_native_executable(path: Path) -> FileIdentity:
    identity = capture_file(path, executable=True)
    if identity.path.name in _SHELL_NAMES:
        raise InvalidExecutableBindingError("shell_executable_forbidden")
    if identity.path.read_bytes()[:2] == b"#!":
        raise InvalidExecutableBindingError("script_executable_forbidden")
    return identity


def capture_python_entrypoint(path: Path) -> tuple[FileIdentity, FileIdentity]:
    entrypoint = capture_file(path, executable=True)
    first_line = entrypoint.path.read_bytes().splitlines()[:1]
    if not first_line or not first_line[0].startswith(b"#!"):
        raise InvalidExecutableBindingError("python_entrypoint_shebang_missing")
    try:
        parts = first_line[0][2:].decode("utf-8", errors="strict").strip().split()
    except UnicodeDecodeError as error:
        raise InvalidExecutableBindingError("python_entrypoint_shebang_invalid") from error
    if len(parts) != 1:
        raise InvalidExecutableBindingError("python_entrypoint_shebang_invalid")
    interpreter_path = Path(parts[0])
    if not interpreter_path.is_absolute() or interpreter_path.name in _SHELL_NAMES:
        raise InvalidExecutableBindingError("shell_interpreter_forbidden")
    try:
        interpreter_realpath = interpreter_path.resolve(strict=True)
    except OSError as error:
        raise InvalidExecutableBindingError("python_interpreter_unavailable") from error
    interpreter = capture_native_executable(interpreter_realpath)
    if not interpreter.path.name.startswith("python"):
        raise InvalidExecutableBindingError("python_interpreter_required")
    return entrypoint, interpreter


def revalidate(identity: FileIdentity, *, executable: bool) -> None:
    if capture_file(identity.path, executable=executable) != identity:
        raise InvalidExecutableBindingError("executable_identity_changed")


def tree_sha256(root: Path) -> str:
    try:
        normalized = root.resolve(strict=True)
    except OSError as error:
        raise InvalidExecutableBindingError("trusted_package_unavailable") from error
    digest = sha256()
    files = tuple(sorted(path for path in normalized.rglob("*.py") if path.is_file() and not path.is_symlink()))
    if not files:
        raise InvalidExecutableBindingError("trusted_package_empty")
    for path in files:
        relative = path.relative_to(normalized).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _descriptor_sha256(descriptor: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = sha256()
    while chunk := os.read(descriptor, 64 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


__all__ = (
    "FileIdentity",
    "InvalidExecutableBindingError",
    "capture_file",
    "capture_native_executable",
    "capture_python_entrypoint",
    "revalidate",
    "tree_sha256",
)

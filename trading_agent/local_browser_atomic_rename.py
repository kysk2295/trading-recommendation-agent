from __future__ import annotations

import ctypes
import errno
import os
import sys
from dataclasses import dataclass
from typing import Final

_DARWIN_RENAME_EXCL: Final = 0x00000004


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK: exceptions need writable traceback state
class AtomicRenameConflictError(RuntimeError):
    """Report an exclusive-rename collision without blocking traceback state."""

    def __str__(self) -> str:
        return "atomic rename destination exists"


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK: exceptions need writable traceback state
class AtomicRenameUnavailableError(RuntimeError):
    """Report unavailable atomic rename while permitting traceback attachment."""

    def __str__(self) -> str:
        return "atomic rename unavailable"


def rename_entry_exclusively(source_directory: int, source: str, destination_directory: int, destination: str) -> None:
    if sys.platform != "darwin":
        raise AtomicRenameUnavailableError()
    try:
        renameatx_np = ctypes.CDLL("libc.dylib", use_errno=True).renameatx_np
    except (AttributeError, OSError):
        raise AtomicRenameUnavailableError() from None
    renameatx_np.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    renameatx_np.restype = ctypes.c_int
    ctypes.set_errno(0)
    if (
        renameatx_np(
            source_directory,
            os.fsencode(source),
            destination_directory,
            os.fsencode(destination),
            _DARWIN_RENAME_EXCL,
        )
        == 0
    ):
        return
    if ctypes.get_errno() in {errno.EEXIST, errno.ENOTEMPTY}:
        raise AtomicRenameConflictError()
    raise AtomicRenameUnavailableError()

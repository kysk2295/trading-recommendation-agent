from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from pydantic import ValidationError

from trading_agent.future_session_kr_supervisor_models import (
    InvalidKrFutureSessionSupervisorError,
    KrFutureSessionSupervisorState,
    canonical_kr_supervisor_state_json,
)


def load_kr_supervisor_state(
    path: Path,
    manifest_hash: str,
) -> KrFutureSessionSupervisorState | None:
    if not path.exists():
        return None
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise InvalidKrFutureSessionSupervisorError
    payload = path.read_bytes()
    try:
        state = KrFutureSessionSupervisorState.model_validate_json(payload)
    except ValidationError:
        raise InvalidKrFutureSessionSupervisorError from None
    if state.manifest_sha256 != manifest_hash or canonical_kr_supervisor_state_json(state).encode() != payload:
        raise InvalidKrFutureSessionSupervisorError
    return state


def persist_kr_supervisor_state(
    path: Path,
    state: KrFutureSessionSupervisorState,
) -> KrFutureSessionSupervisorState:
    payload = canonical_kr_supervisor_state_json(state).encode()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_atomic(path, payload)
    _write_atomic(path.with_name("kr-supervisor-report.json"), payload)
    return state


def _write_atomic(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    descriptor_open = True
    try:
        os.fchmod(descriptor, 0o600)
        if os.write(descriptor, payload) != len(payload):
            raise OSError("short KR supervisor state write")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor_open = False
        os.replace(temporary, path)
    finally:
        if descriptor_open:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


__all__ = ("load_kr_supervisor_state", "persist_kr_supervisor_state")

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import time
from contextlib import suppress
from pathlib import Path
from typing import Final

_FILE_MODE: Final = 0o600
_DIRECTORY_MODE: Final = 0o700
_MAX_FILE_BYTES: Final = 64 * 1024 * 1024
_STAGING_SUFFIX: Final = ".staging"
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_COMMIT: Final = re.compile(r"^[0-9a-f]{40}$")
_ROLES: Final = {
    "us": frozenset(
        {
            "us_orb_watcher",
            "us_hermes_projection",
            "us_day_preflight_observer",
            "us_day_close_finalizer",
            "us_day_arm_observer",
            "us_research_post_close_swing",
        }
    ),
    "kr": frozenset({"kr_supervisor"}),
}


class IncidentPublicationError(ValueError):
    pass


def publish_execution_incident(
    *,
    receipt_path: Path,
    queue_path: Path,
    manifest_path: Path,
    market: str,
    target_session: dt.date,
    role: str,
    request_sha256: str,
    plan_sha256: str,
    scheduler_main_sha: str,
    runtime_commit_sha: str,
) -> None:
    state_root = _validate_inputs(
        receipt_path,
        queue_path,
        manifest_path,
        market,
        target_session,
        role,
        request_sha256,
        plan_sha256,
        scheduler_main_sha,
        runtime_commit_sha,
    )
    state = _open_private_directory(state_root)
    children: list[int] = []
    try:
        fcntl.flock(state, fcntl.LOCK_EX)
        artifacts = _open_child_directory(state, "artifacts")
        children.append(artifacts)
        market_directory = _open_child_directory(artifacts, market)
        children.append(market_directory)
        session = _open_child_directory(market_directory, target_session.isoformat())
        children.append(session)
        incidents = _open_child_directory(session, "execution-incidents")
        children.append(incidents)
        queue = _open_child_directory(state, "pending-execution-incidents")
        children.append(queue)

        manifest_payload = _read_private_file(session, "preparation-manifest.json")
        candidate = {
            "completed_at_epoch": int(time.time()),
            "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
            "market": market,
            "plan_sha256": plan_sha256,
            "reason": "runtime_authority_invalid",
            "request_sha256": request_sha256,
            "role": role,
            "runtime_commit_sha": runtime_commit_sha,
            "scheduler_main_sha": scheduler_main_sha,
            "schema_version": 1,
            "target_session": target_session.isoformat(),
        }
        receipt_payload = _publish_receipt(incidents, receipt_path.name, candidate)
        _require_tree_binding(
            state_root,
            state,
            artifacts,
            market,
            market_directory,
            target_session.isoformat(),
            session,
            incidents,
            queue,
        )
        pointer = {
            "incident_sha256": hashlib.sha256(receipt_payload).hexdigest(),
            "market": market,
            "role": role,
            "schema_version": 1,
            "target_session": target_session.isoformat(),
        }
        _publish_exact(queue, queue_path.name, _canonical_json(pointer))
        _require_tree_binding(
            state_root,
            state,
            artifacts,
            market,
            market_directory,
            target_session.isoformat(),
            session,
            incidents,
            queue,
        )
    finally:
        for descriptor in reversed(children):
            os.close(descriptor)
        with suppress(OSError):
            fcntl.flock(state, fcntl.LOCK_UN)
        os.close(state)


def _validate_inputs(
    receipt: Path,
    queue: Path,
    manifest: Path,
    market: str,
    target: dt.date,
    role: str,
    request_sha256: str,
    plan_sha256: str,
    scheduler_main_sha: str,
    runtime_commit_sha: str,
) -> Path:
    paths = (receipt, queue, manifest)
    if any(not path.is_absolute() or Path(os.path.abspath(path)) != path for path in paths):
        raise IncidentPublicationError
    try:
        state_root = receipt.parents[4]
    except IndexError:
        raise IncidentPublicationError from None
    if (
        market not in _ROLES
        or role not in _ROLES[market]
        or _SHA256.fullmatch(request_sha256) is None
        or _SHA256.fullmatch(plan_sha256) is None
        or _COMMIT.fullmatch(scheduler_main_sha) is None
        or _COMMIT.fullmatch(runtime_commit_sha) is None
        or receipt != state_root / "artifacts" / market / target.isoformat() / "execution-incidents" / f"{role}.json"
        or manifest != receipt.parent.parent / "preparation-manifest.json"
        or queue != state_root / "pending-execution-incidents" / f"{market}--{target.isoformat()}--{role}.json"
    ):
        raise IncidentPublicationError
    return state_root


def _open_private_directory(path: Path) -> int:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _require_private_directory(descriptor)
        return descriptor
    except (OSError, ValueError):
        os.close(descriptor)
        raise


def _open_child_directory(parent: int, name: str) -> int:
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        _require_private_directory(descriptor)
        return descriptor
    except (OSError, ValueError):
        os.close(descriptor)
        raise


def _require_private_directory(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != _DIRECTORY_MODE
    ):
        raise IncidentPublicationError


def _read_private_file(parent: int, name: str, links: tuple[int, ...] = (1,)) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != _FILE_MODE
            or before.st_nlink not in links
            or before.st_size < 0
            or before.st_size > _MAX_FILE_BYTES
        ):
            raise IncidentPublicationError
        payload = bytearray()
        while chunk := os.read(descriptor, min(64 * 1024, _MAX_FILE_BYTES + 1 - len(payload))):
            payload.extend(chunk)
            if len(payload) > _MAX_FILE_BYTES:
                raise IncidentPublicationError
        after = os.fstat(descriptor)
        if (
            len(payload) != before.st_size
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
        ):
            raise IncidentPublicationError
        return bytes(payload)
    finally:
        os.close(descriptor)


def _publish_receipt(parent: int, name: str, candidate: dict[str, object]) -> bytes:
    try:
        existing = _read_private_file(parent, name, (1, 2))
    except FileNotFoundError:
        payload = _canonical_json(candidate)
        _publish_new(parent, name, payload)
        return payload
    stored = _canonical_object(existing)
    completed = stored.pop("completed_at_epoch", None)
    expected = dict(candidate)
    _ = expected.pop("completed_at_epoch")
    if type(completed) is not int or completed < 0 or stored != expected:
        raise IncidentPublicationError
    _repair_stage_alias(parent, name)
    return existing


def _publish_exact(parent: int, name: str, payload: bytes) -> None:
    try:
        existing = _read_private_file(parent, name, (1, 2))
    except FileNotFoundError:
        _publish_new(parent, name, payload)
        return
    if existing != payload:
        raise IncidentPublicationError
    _repair_stage_alias(parent, name)


def _publish_new(parent: int, name: str, payload: bytes) -> None:
    _remove_orphan_staging(parent, name)
    stage = f".{name}.{secrets.token_hex(12)}{_STAGING_SUFFIX}"
    descriptor = os.open(
        stage,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        _FILE_MODE,
        dir_fd=parent,
    )
    try:
        os.fchmod(descriptor, _FILE_MODE)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise IncidentPublicationError
            view = view[written:]
        os.fsync(descriptor)
        os.link(stage, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        published = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            if not _same_file(descriptor, published):
                raise IncidentPublicationError
            os.fsync(parent)
            os.unlink(stage, dir_fd=parent)
            os.fsync(parent)
            if os.fstat(descriptor).st_nlink != 1 or not _same_named_file(parent, name, descriptor):
                raise IncidentPublicationError
        finally:
            os.close(published)
    finally:
        os.close(descriptor)
        with suppress(FileNotFoundError):
            os.unlink(stage, dir_fd=parent)
        os.fsync(parent)


def _repair_stage_alias(parent: int, name: str) -> None:
    final = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        identity = os.fstat(final)
        if identity.st_nlink == 2:
            prefix = f".{name}."
            matches = [
                candidate
                for candidate in os.listdir(parent)
                if candidate.startswith(prefix)
                and candidate.endswith(_STAGING_SUFFIX)
                and _named_identity(parent, candidate) == (identity.st_dev, identity.st_ino)
            ]
            if len(matches) != 1:
                raise IncidentPublicationError
            os.unlink(matches[0], dir_fd=parent)
            os.fsync(parent)
        if os.fstat(final).st_nlink != 1 or not _same_named_file(parent, name, final):
            raise IncidentPublicationError
    finally:
        os.close(final)


def _remove_orphan_staging(parent: int, name: str) -> None:
    prefix = f".{name}."
    removed = False
    for candidate in os.listdir(parent):
        if not candidate.startswith(prefix) or not candidate.endswith(_STAGING_SUFFIX):
            continue
        metadata = os.stat(candidate, dir_fd=parent, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != _FILE_MODE
            or metadata.st_nlink != 1
        ):
            raise IncidentPublicationError
        os.unlink(candidate, dir_fd=parent)
        removed = True
    if removed:
        os.fsync(parent)


def _require_tree_binding(
    root: Path,
    state: int,
    artifacts: int,
    market_name: str,
    market: int,
    session_name: str,
    session: int,
    incidents: int,
    queue: int,
) -> None:
    root_metadata = os.stat(root, follow_symlinks=False)
    if (root_metadata.st_dev, root_metadata.st_ino) != _identity(state):
        raise IncidentPublicationError
    _require_child_binding(state, "artifacts", artifacts)
    _require_child_binding(artifacts, market_name, market)
    _require_child_binding(market, session_name, session)
    _require_child_binding(session, "execution-incidents", incidents)
    _require_child_binding(state, "pending-execution-incidents", queue)


def _require_child_binding(parent: int, name: str, expected: int) -> None:
    observed = _open_child_directory(parent, name)
    try:
        if _identity(observed) != _identity(expected):
            raise IncidentPublicationError
    finally:
        os.close(observed)


def _canonical_object(payload: bytes) -> dict[str, object]:
    try:
        value = json.loads(payload)
    except (TypeError, UnicodeError, json.JSONDecodeError):
        raise IncidentPublicationError from None
    if not isinstance(value, dict) or _canonical_json(value) != payload:
        raise IncidentPublicationError
    return value


def _canonical_json(value: dict[str, object]) -> bytes:
    return (json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode()


def _identity(descriptor: int) -> tuple[int, int]:
    metadata = os.fstat(descriptor)
    return metadata.st_dev, metadata.st_ino


def _named_identity(parent: int, name: str) -> tuple[int, int]:
    metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
    return metadata.st_dev, metadata.st_ino


def _same_file(left: int, right: int) -> bool:
    return _identity(left) == _identity(right)


def _same_named_file(parent: int, name: str, descriptor: int) -> bool:
    return _named_identity(parent, name) == _identity(descriptor)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--market", required=True, choices=("us", "kr"))
    parser.add_argument("--target-session", required=True, type=dt.date.fromisoformat)
    parser.add_argument("--role", required=True)
    parser.add_argument("--request-sha256", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--scheduler-main-sha", required=True)
    parser.add_argument("--runtime-commit-sha", required=True)
    return parser


def main(arguments: tuple[str, ...] | None = None) -> int:
    parsed = build_parser().parse_args(arguments)
    try:
        publish_execution_incident(
            receipt_path=parsed.receipt,
            queue_path=parsed.queue,
            manifest_path=parsed.manifest,
            market=parsed.market,
            target_session=parsed.target_session,
            role=parsed.role,
            request_sha256=parsed.request_sha256,
            plan_sha256=parsed.plan_sha256,
            scheduler_main_sha=parsed.scheduler_main_sha,
            runtime_commit_sha=parsed.runtime_commit_sha,
        )
    except (OSError, TypeError, ValueError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("IncidentPublicationError", "build_parser", "main", "publish_execution_incident")

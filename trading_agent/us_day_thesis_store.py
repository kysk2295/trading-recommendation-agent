from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import override

from pydantic import ValidationError

from trading_agent.private_directory_identity import open_private_parent, require_private_directory
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.us_day_thesis_models import UsDayThesisChange, UsDayTradeThesis


class InvalidUsDayThesisStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "US day thesis store is invalid"


class UsDayThesisStore:
    """Bind private store paths to durable inode records.

    This detects accidental or untrusted rename/replacement, link, ownership, and
    permission changes. It assumes the current-user-only parent holding the
    external anchor is not maliciously rewritten by that same OS user.
    """

    def __init__(self, root: Path) -> None:
        self.root = _absolute_without_symlinks(root)
        self._anchor = self.root.parent / f".{self.root.name}.us-day-thesis-root.json"
        self._identity_lock_path = self.root.parent / f".{self.root.name}.us-day-thesis-root.lock"
        try:
            self._root_identity, self._registry_identity, self._identity_payload = self._initialize_or_verify_root()
        except (InvalidPrivateImmutableFileError, OSError, TypeError, ValueError):
            raise InvalidUsDayThesisStoreError from None

    def publish_thesis(self, thesis: UsDayTradeThesis) -> bool:
        return self._publish(self.root / "theses" / f"{thesis.thesis_id}.json", thesis.model_dump_json())

    def publish_change(self, change: UsDayThesisChange) -> bool:
        try:
            with self._chain_lock(change.thesis_id):
                return self._publish_change_locked(change)
        except InvalidUsDayThesisStoreError:
            raise
        except (OSError, ValueError):
            raise InvalidUsDayThesisStoreError from None

    def _publish_change_locked(self, change: UsDayThesisChange) -> bool:
        thesis = self.thesis(change.thesis_id)
        prior = self.changes(change.thesis_id)
        existing = next((item for item in prior if item.event_id == change.event_id), None)
        if existing is not None:
            if existing != change:
                raise InvalidUsDayThesisStoreError
            return False
        latest_id = thesis.thesis_id if not prior else prior[-1].event_id
        latest_time = thesis.observed_at if not prior else prior[-1].occurred_at
        terminal = {"cancel_entry", "invalidate_logic", "close"}
        if (
            change.parent_event_id != latest_id
            or change.occurred_at < latest_time
            or (prior and prior[-1].kind.value in terminal)
        ):
            raise InvalidUsDayThesisStoreError
        return self._publish(
            self.root / "changes" / change.thesis_id / f"{change.event_id}.json",
            change.model_dump_json(),
        )

    @contextmanager
    def _chain_lock(self, thesis_id: str) -> Iterator[None]:
        directory = self.root / "changes" / thesis_id
        self._prepare_bound_directory(directory, create=True)
        parent = open_private_parent(directory, create=True)
        descriptor = -1
        try:
            require_private_directory(parent)
            descriptor = os.open(
                ".chain.lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
                raise InvalidUsDayThesisStoreError
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent)

    def publish_terminal_card(self, thesis: UsDayTradeThesis, markdown: str) -> bool:
        if thesis.symbol is not None or not markdown.strip():
            raise InvalidUsDayThesisStoreError
        _ = self.thesis(thesis.thesis_id)
        return self._publish(self.root / "terminal_cards" / f"{thesis.thesis_id}.md", markdown)

    def thesis(self, thesis_id: str) -> UsDayTradeThesis:
        try:
            self._prepare_bound_directory(self.root / "theses", create=False)
            payload = read_private_text(self.root / "theses" / f"{thesis_id}.json")
            thesis = UsDayTradeThesis.model_validate_json(payload)
            if thesis.thesis_id != thesis_id:
                raise InvalidUsDayThesisStoreError
            return thesis
        except (InvalidPrivateImmutableFileError, OSError, ValidationError, ValueError):
            raise InvalidUsDayThesisStoreError from None

    def theses(self) -> tuple[UsDayTradeThesis, ...]:
        try:
            directory = self.root / "theses"
            self._prepare_bound_directory(directory, create=False)
            if not directory.exists():
                return ()
            return tuple(self.thesis(path.stem) for path in sorted(directory.glob("*.json")))
        except (OSError, ValueError):
            raise InvalidUsDayThesisStoreError from None

    def changes(self, thesis_id: str) -> tuple[UsDayThesisChange, ...]:
        try:
            directory = self.root / "changes" / thesis_id
            self._prepare_bound_directory(directory, create=False)
            if not directory.exists():
                return ()
            paths = tuple(sorted(directory.glob("*.json")))
            changes = tuple(UsDayThesisChange.model_validate_json(read_private_text(path)) for path in paths)
            if any(
                item.thesis_id != thesis_id or item.event_id != path.stem
                for item, path in zip(changes, paths, strict=True)
            ):
                raise InvalidUsDayThesisStoreError
            ordered: list[UsDayThesisChange] = []
            parent = thesis_id
            parent_time = self.thesis(thesis_id).observed_at
            terminal = {"cancel_entry", "invalidate_logic", "close"}
            remaining = {item.event_id: item for item in changes}
            while remaining:
                children = tuple(item for item in remaining.values() if item.parent_event_id == parent)
                if len(children) != 1 or (ordered and ordered[-1].kind.value in terminal):
                    raise InvalidUsDayThesisStoreError
                child = children[0]
                if child.occurred_at < parent_time:
                    raise InvalidUsDayThesisStoreError
                ordered.append(child)
                del remaining[child.event_id]
                parent = child.event_id
                parent_time = child.occurred_at
            return tuple(ordered)
        except (InvalidPrivateImmutableFileError, OSError, ValidationError, ValueError):
            raise InvalidUsDayThesisStoreError from None

    def _publish(self, path: Path, payload: str) -> bool:
        try:
            self._prepare_bound_directory(path.parent, create=True)
            return publish_private_immutable_text(path, payload)
        except InvalidPrivateImmutableFileError:
            raise InvalidUsDayThesisStoreError from None

    def _initialize_or_verify_root(self) -> tuple[tuple[int, int], tuple[int, int], str]:
        with self._identity_lock():
            anchor_exists = os.path.lexists(self._anchor)
            root_exists = os.path.lexists(self.root)
            if anchor_exists:
                if not root_exists:
                    raise InvalidUsDayThesisStoreError
                payload = read_private_text(self._anchor)
                return self._verify_identity_payload(payload)
            if root_exists:
                _require_private_directory_path(self.root)
                if tuple(self.root.iterdir()):
                    raise InvalidUsDayThesisStoreError
            else:
                self.root.mkdir(mode=0o700)
            registry = self.root / ".identity-registry"
            registry.mkdir(mode=0o700)
            token = secrets.token_hex(32)
            payload = _identity_payload(self.root, registry, token)
            marker = self.root / ".store-identity.json"
            if not publish_private_immutable_text(marker, payload):
                raise InvalidUsDayThesisStoreError
            if not publish_private_immutable_text(self._anchor, payload):
                raise InvalidUsDayThesisStoreError
            return _directory_identity(self.root), _directory_identity(registry), payload

    def _verify_identity_payload(self, payload: str) -> tuple[tuple[int, int], tuple[int, int], str]:
        try:
            decoded = json.loads(payload)
            registry = self.root / ".identity-registry"
            marker = self.root / ".store-identity.json"
            root_identity = _directory_identity(self.root)
            registry_identity = _directory_identity(registry)
            if (
                decoded != json.loads(_identity_payload(self.root, registry, str(decoded["token"])))
                or read_private_text(marker) != payload
            ):
                raise InvalidUsDayThesisStoreError
            return root_identity, registry_identity, payload
        except (KeyError, TypeError, json.JSONDecodeError, InvalidPrivateImmutableFileError, ValueError):
            raise InvalidUsDayThesisStoreError from None

    def _verify_root_binding(self) -> None:
        try:
            if (
                _directory_identity(self.root) != self._root_identity
                or _directory_identity(self.root / ".identity-registry") != self._registry_identity
                or read_private_text(self._anchor) != self._identity_payload
                or read_private_text(self.root / ".store-identity.json") != self._identity_payload
            ):
                raise InvalidUsDayThesisStoreError
        except (InvalidPrivateImmutableFileError, OSError, ValueError):
            raise InvalidUsDayThesisStoreError from None

    def _prepare_bound_directory(self, directory: Path, *, create: bool) -> None:
        with self._identity_lock():
            self._verify_root_binding()
            try:
                relative = directory.relative_to(self.root)
            except ValueError:
                raise InvalidUsDayThesisStoreError from None
            current = self.root
            for component in relative.parts:
                current /= component
                created = False
                if not os.path.lexists(current):
                    if not create:
                        return
                    current.mkdir(mode=0o700)
                    created = True
                _require_private_directory_path(current)
                identity = _directory_identity(current)
                identity_path = self.root / ".identity-registry" / f"{_namespace_key(current, self.root)}.json"
                expected = _namespace_payload(current, self.root, identity, self._identity_payload)
                if os.path.lexists(identity_path):
                    if read_private_text(identity_path) != expected:
                        raise InvalidUsDayThesisStoreError
                else:
                    if not created or tuple(current.iterdir()):
                        raise InvalidUsDayThesisStoreError
                    if not publish_private_immutable_text(identity_path, expected):
                        raise InvalidUsDayThesisStoreError

    @contextmanager
    def _identity_lock(self) -> Iterator[None]:
        _require_safe_creation_path(self.root.parent, self.root.parent)
        descriptor = os.open(
            self._identity_lock_path,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
                raise InvalidUsDayThesisStoreError
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)


__all__ = ("InvalidUsDayThesisStoreError", "UsDayThesisStore")


def _absolute_without_symlinks(root: Path) -> Path:
    absolute = Path(os.path.abspath(root.expanduser()))
    current = Path(absolute.anchor)
    try:
        for component in absolute.parts[1:]:
            current /= component
            try:
                metadata = os.lstat(current)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode):
                raise InvalidUsDayThesisStoreError
        _require_safe_creation_path(absolute, absolute)
        return absolute
    except (OSError, ValueError):
        raise InvalidUsDayThesisStoreError from None


def _require_safe_creation_path(path: Path, store_root: Path) -> None:
    try:
        path.relative_to(store_root)
    except ValueError:
        if path != store_root:
            raise InvalidUsDayThesisStoreError from None
    current = path
    while not os.path.lexists(current):
        if current == current.parent:
            raise InvalidUsDayThesisStoreError
        current = current.parent
    if current != store_root and os.path.lexists(store_root):
        current = store_root
    limit = path
    while True:
        metadata = os.lstat(current)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise InvalidUsDayThesisStoreError
        if current == limit:
            return
        relative = limit.relative_to(current)
        next_path = current / relative.parts[0]
        if not os.path.lexists(next_path):
            return
        current = next_path


def _require_private_directory_path(path: Path) -> None:
    metadata = os.lstat(path)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise InvalidUsDayThesisStoreError


def _directory_identity(path: Path) -> tuple[int, int]:
    _require_private_directory_path(path)
    metadata = os.lstat(path)
    return metadata.st_dev, metadata.st_ino


def _identity_payload(root: Path, registry: Path, token: str) -> str:
    root_device, root_inode = _directory_identity(root)
    registry_device, registry_inode = _directory_identity(registry)
    return json.dumps(
        {
            "root": str(root),
            "root_device": root_device,
            "root_inode": root_inode,
            "registry_device": registry_device,
            "registry_inode": registry_inode,
            "token": token,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _namespace_key(path: Path, root: Path) -> str:
    return hashlib.sha256(str(path.relative_to(root)).encode()).hexdigest()


def _namespace_payload(path: Path, root: Path, identity: tuple[int, int], root_payload: str) -> str:
    return json.dumps(
        {
            "relative_path": str(path.relative_to(root)),
            "device": identity[0],
            "inode": identity[1],
            "root_identity_sha256": hashlib.sha256(root_payload.encode()).hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )

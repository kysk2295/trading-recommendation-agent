from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.alpaca_sip_quote_actionability_artifact import AlpacaSipQuoteActionabilityArtifact
from trading_agent.alpaca_sip_quote_actionability_creation import AlpacaSipQuoteActionabilityCreation
from trading_agent.alpaca_sip_quote_actionability_manifest import (
    AlpacaSipQuoteActionabilityManifest,
    read_alpaca_sip_quote_actionability_manifest,
)
from trading_agent.alpaca_sip_quote_actionability_store import AlpacaSipQuoteActionabilityStore

_MANIFEST_NAME = re.compile(r"^[0-9a-f]{64}\.json$", flags=re.ASCII)
_RECEIPT_NAME = re.compile(r"^[0-9a-f]{64}\.sqlite3$", flags=re.ASCII)
_RECEIPT_LOCK_NAME = re.compile(r"^([0-9a-f]{64})\.sqlite3\.(?:owner|writer)\.lock$", flags=re.ASCII)


class RuntimeLiveEvidenceInventoryError(ValueError):
    @override
    def __str__(self) -> str:
        return "runtime live evidence inventory is invalid"


@dataclass(frozen=True, slots=True)
class RuntimeLiveEvidenceInventory:
    manifests: tuple[AlpacaSipQuoteActionabilityManifest, ...]
    receipts: tuple[tuple[str, Path], ...]
    artifacts: tuple[AlpacaSipQuoteActionabilityArtifact, ...]
    creations: tuple[AlpacaSipQuoteActionabilityCreation, ...]


def load_runtime_live_evidence_inventory(
    manifest_root: Path,
    receipt_root: Path,
    actionability_store: Path,
) -> RuntimeLiveEvidenceInventory:
    try:
        manifests = _read_manifests(manifest_root)
        store = AlpacaSipQuoteActionabilityStore(actionability_store)
        return RuntimeLiveEvidenceInventory(
            manifests,
            _read_receipts(receipt_root, manifests),
            store.records(),
            store.creations(),
        )
    except (AttributeError, OSError, TypeError, ValueError):
        raise RuntimeLiveEvidenceInventoryError from None


def _read_manifests(root: Path) -> tuple[AlpacaSipQuoteActionabilityManifest, ...]:
    source = root.expanduser().absolute()
    if not source.exists():
        if source.is_symlink():
            raise RuntimeLiveEvidenceInventoryError
        return ()
    _require_directory(source, private=False)
    manifests: list[AlpacaSipQuoteActionabilityManifest] = []
    for path in sorted(source.iterdir()):
        if _MANIFEST_NAME.fullmatch(path.name) is None:
            raise RuntimeLiveEvidenceInventoryError
        manifest = read_alpaca_sip_quote_actionability_manifest(path)
        if path.stem != _digest(manifest):
            raise RuntimeLiveEvidenceInventoryError
        manifests.append(manifest)
    return tuple(manifests)


def _read_receipts(
    root: Path,
    manifests: tuple[AlpacaSipQuoteActionabilityManifest, ...],
) -> tuple[tuple[str, Path], ...]:
    source = root.expanduser().absolute()
    if not source.exists():
        if source.is_symlink():
            raise RuntimeLiveEvidenceInventoryError
        return ()
    _require_directory(source, private=True)
    manifest_digests = {_digest(manifest) for manifest in manifests}
    receipts: list[tuple[str, Path]] = []
    for path in sorted(source.iterdir()):
        receipt_match = _RECEIPT_NAME.fullmatch(path.name)
        lock_match = _RECEIPT_LOCK_NAME.fullmatch(path.name)
        if receipt_match is not None and path.stem in manifest_digests:
            _require_private_file(path)
            receipts.append((path.stem, path))
            continue
        if lock_match is not None and lock_match.group(1) in manifest_digests:
            _require_private_file(path)
            continue
        raise RuntimeLiveEvidenceInventoryError
    return tuple(receipts)


def _require_directory(path: Path, *, private: bool) -> None:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or (private and stat.S_IMODE(metadata.st_mode) != 0o700)
    ):
        raise RuntimeLiveEvidenceInventoryError


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise RuntimeLiveEvidenceInventoryError


def _digest(manifest: AlpacaSipQuoteActionabilityManifest) -> str:
    return manifest.manifest_id.rpartition(":")[2]


__all__ = (
    "RuntimeLiveEvidenceInventory",
    "RuntimeLiveEvidenceInventoryError",
    "load_runtime_live_evidence_inventory",
)

from __future__ import annotations

import datetime as dt
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.alpaca_sip_dynamic_receipt_models import (
    AlpacaSipDynamicReceiptError,
    AlpacaSipDynamicTerminalStatus,
)
from trading_agent.alpaca_sip_dynamic_terminal_store import AlpacaSipDynamicTerminalStore
from trading_agent.alpaca_sip_quote_actionability_artifact import AlpacaSipQuoteActionabilityArtifact
from trading_agent.alpaca_sip_quote_actionability_manifest import (
    AlpacaSipQuoteActionabilityManifest,
    read_alpaca_sip_quote_actionability_manifest,
)
from trading_agent.alpaca_sip_quote_actionability_store import AlpacaSipQuoteActionabilityStore
from trading_agent.us_runtime_minute_supervisor_models import RuntimeMinuteSupervisorRecord
from trading_agent.us_runtime_minute_supervisor_store import RuntimeMinuteSupervisorStore
from trading_agent.us_runtime_supervisor_live_audit import (
    RuntimeSupervisorLiveAudit,
    RuntimeSupervisorLiveStatus,
)

_MANIFEST_NAME = re.compile(r"^[0-9a-f]{64}\.json$", flags=re.ASCII)
_RECEIPT_NAME = re.compile(r"^[0-9a-f]{64}\.sqlite3$", flags=re.ASCII)
_RECEIPT_LOCK_NAME = re.compile(r"^([0-9a-f]{64})\.sqlite3\.(?:owner|writer)\.lock$", flags=re.ASCII)
type _TerminalKey = tuple[str, dt.datetime]


class RuntimeLiveEvidenceVerificationError(ValueError):
    @override
    def __str__(self) -> str:
        return "runtime live evidence verification is blocked"


@dataclass(frozen=True, slots=True)
class RuntimeLiveEvidenceVerificationRequest:
    supervisor_store: Path
    manifest_root: Path
    receipt_root: Path
    actionability_store: Path


@dataclass(frozen=True, slots=True)
class RuntimeLiveEvidenceVerificationResult:
    completed_attempt_count: int
    selected_manifest_count: int
    created_terminal_count: int
    replay_terminal_count: int
    actionability_artifact_count: int


@dataclass(frozen=True, slots=True)
class _EvidenceInventory:
    manifests: tuple[AlpacaSipQuoteActionabilityManifest, ...]
    receipts: tuple[tuple[str, Path], ...]
    artifacts: tuple[AlpacaSipQuoteActionabilityArtifact, ...]


def verify_runtime_live_evidence(
    request: RuntimeLiveEvidenceVerificationRequest,
) -> RuntimeLiveEvidenceVerificationResult:
    try:
        _validate_request(request)
        parents = RuntimeMinuteSupervisorStore(request.supervisor_store).records()
        children = RuntimeMinuteSupervisorStore(request.supervisor_store).live_records()
        paired = _paired_history(parents, children)
        manifests = _read_manifests(request.manifest_root)
        receipts = _read_receipts(request.receipt_root, manifests)
        artifacts = AlpacaSipQuoteActionabilityStore(request.actionability_store).records()
        artifact_keys = tuple(_artifact_key(artifact) for artifact in artifacts)
        if len(artifact_keys) != len(set(artifact_keys)):
            raise RuntimeLiveEvidenceVerificationError
        inventory = _EvidenceInventory(manifests, receipts, artifacts)
        completed = 0
        selected = 0
        created = 0
        replay = 0
        used_keys: set[_TerminalKey] = set()
        for parent, child in paired:
            if child.status is not RuntimeSupervisorLiveStatus.COMPLETED:
                continue
            current = tuple(manifest for manifest in manifests if manifest.snapshot.observed_at == parent.started_at)
            instruments = tuple(manifest.snapshot.instrument_id for manifest in current)
            keys = tuple(_manifest_key(manifest) for manifest in current)
            if (
                len(current) != child.selected_count
                or len(instruments) != len(set(instruments))
                or len(keys) != len(set(keys))
            ):
                raise RuntimeLiveEvidenceVerificationError
            current_created = sum(_verify_manifest(manifest, parent, inventory) for manifest in current)
            current_replay = len(current) - current_created
            if current_created != child.created_count or current_replay != child.replay_count:
                raise RuntimeLiveEvidenceVerificationError
            completed += 1
            selected += len(current)
            created += current_created
            replay += current_replay
            used_keys.update(keys)
        return RuntimeLiveEvidenceVerificationResult(completed, selected, created, replay, len(used_keys))
    except (AlpacaSipDynamicReceiptError, AttributeError, OSError, TypeError, ValueError):
        raise RuntimeLiveEvidenceVerificationError from None


def _verify_manifest(
    manifest: AlpacaSipQuoteActionabilityManifest,
    parent: RuntimeMinuteSupervisorRecord,
    inventory: _EvidenceInventory,
) -> bool:
    key = _manifest_key(manifest)
    matches = tuple(artifact for artifact in inventory.artifacts if _artifact_key(artifact) == key)
    if len(matches) != 1:
        raise RuntimeLiveEvidenceVerificationError
    artifact = matches[0]
    trade = artifact.bundle.trade_confirmation
    if (
        artifact.base_publication != manifest.base_publication
        or artifact.assessment.evaluated_at != trade.observed_at
        or trade.dynamic_plan_id != manifest.plan.plan_id
        or trade.instrument_id != manifest.snapshot.instrument_id
        or trade.symbol != manifest.base_publication.signal.symbol
    ):
        raise RuntimeLiveEvidenceVerificationError
    source_manifests = tuple(
        source
        for source in inventory.manifests
        if _manifest_key(source) == key
        and source.base_publication == artifact.base_publication
        and source.plan.plan_id == trade.dynamic_plan_id
        and source.snapshot.identity.identity_sha256 == trade.research_input_identity_sha256
        and source.snapshot.instrument_id == trade.instrument_id
    )
    source_receipts = tuple(
        path
        for source in source_manifests
        for receipt_digest, path in inventory.receipts
        if receipt_digest == _digest(source)
    )
    if len(source_receipts) != 1:
        raise RuntimeLiveEvidenceVerificationError
    history = AlpacaSipDynamicTerminalStore(source_receipts[0]).load_history(manifest.plan)
    if len(history) != 1:
        raise RuntimeLiveEvidenceVerificationError
    terminal = history[0]
    if (
        terminal.status is not AlpacaSipDynamicTerminalStatus.BOUNDED_COMPLETE
        or terminal.plan_id != trade.dynamic_plan_id
        or terminal.connection_epoch != trade.connection_epoch
        or terminal.terminal_at != trade.observed_at
    ):
        raise RuntimeLiveEvidenceVerificationError
    if parent.started_at <= terminal.terminal_at <= parent.finished_at:
        return True
    if terminal.terminal_at < parent.started_at:
        return False
    raise RuntimeLiveEvidenceVerificationError


def _paired_history(
    parents: tuple[RuntimeMinuteSupervisorRecord, ...],
    children: tuple[RuntimeSupervisorLiveAudit, ...],
) -> tuple[tuple[RuntimeMinuteSupervisorRecord, RuntimeSupervisorLiveAudit], ...]:
    offset = len(parents) - len(children)
    if offset < 0 or tuple(child.attempt_id for child in children) != tuple(
        parent.attempt_id for parent in parents[offset:]
    ):
        raise RuntimeLiveEvidenceVerificationError
    return tuple(zip(parents[offset:], children, strict=True))


def _read_manifests(root: Path) -> tuple[AlpacaSipQuoteActionabilityManifest, ...]:
    source = root.expanduser().absolute()
    if not source.exists():
        if source.is_symlink():
            raise RuntimeLiveEvidenceVerificationError
        return ()
    _require_directory(source, private=False)
    manifests: list[AlpacaSipQuoteActionabilityManifest] = []
    for path in sorted(source.iterdir()):
        if _MANIFEST_NAME.fullmatch(path.name) is None:
            raise RuntimeLiveEvidenceVerificationError
        manifest = read_alpaca_sip_quote_actionability_manifest(path)
        if path.stem != _digest(manifest):
            raise RuntimeLiveEvidenceVerificationError
        manifests.append(manifest)
    return tuple(manifests)


def _read_receipts(
    root: Path,
    manifests: tuple[AlpacaSipQuoteActionabilityManifest, ...],
) -> tuple[tuple[str, Path], ...]:
    source = root.expanduser().absolute()
    if not source.exists():
        if source.is_symlink():
            raise RuntimeLiveEvidenceVerificationError
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
        raise RuntimeLiveEvidenceVerificationError
    return tuple(receipts)


def _require_directory(path: Path, *, private: bool) -> None:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or (private and stat.S_IMODE(metadata.st_mode) != 0o700)
    ):
        raise RuntimeLiveEvidenceVerificationError


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise RuntimeLiveEvidenceVerificationError


def _manifest_key(manifest: AlpacaSipQuoteActionabilityManifest) -> _TerminalKey:
    return manifest.base_publication.signal.signal_id, manifest.scan_started_at


def _artifact_key(artifact: AlpacaSipQuoteActionabilityArtifact) -> _TerminalKey:
    return artifact.base_publication.signal.signal_id, artifact.assessment.scan_started_at


def _digest(manifest: AlpacaSipQuoteActionabilityManifest) -> str:
    return manifest.manifest_id.rpartition(":")[2]


def _validate_request(request: RuntimeLiveEvidenceVerificationRequest) -> None:
    if type(request) is not RuntimeLiveEvidenceVerificationRequest or any(
        not isinstance(path, Path)
        for path in (
            request.supervisor_store,
            request.manifest_root,
            request.receipt_root,
            request.actionability_store,
        )
    ):
        raise RuntimeLiveEvidenceVerificationError


__all__ = (
    "RuntimeLiveEvidenceVerificationError",
    "RuntimeLiveEvidenceVerificationRequest",
    "RuntimeLiveEvidenceVerificationResult",
    "verify_runtime_live_evidence",
)

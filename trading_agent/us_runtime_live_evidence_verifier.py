from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.alpaca_sip_dynamic_receipt_models import (
    AlpacaSipDynamicReceiptError,
    AlpacaSipDynamicTerminalStatus,
)
from trading_agent.alpaca_sip_dynamic_terminal_store import AlpacaSipDynamicTerminalStore
from trading_agent.alpaca_sip_quote_actionability_artifact import AlpacaSipQuoteActionabilityArtifact
from trading_agent.alpaca_sip_quote_actionability_creation import AlpacaSipQuoteActionabilityCreation
from trading_agent.alpaca_sip_quote_actionability_manifest import (
    AlpacaSipQuoteActionabilityManifest,
)
from trading_agent.us_runtime_live_evidence_inventory import (
    RuntimeLiveEvidenceInventory,
    load_runtime_live_evidence_inventory,
)
from trading_agent.us_runtime_minute_supervisor_models import RuntimeMinuteSupervisorRecord
from trading_agent.us_runtime_minute_supervisor_store import RuntimeMinuteSupervisorStore
from trading_agent.us_runtime_supervisor_live_audit import (
    RuntimeSupervisorLiveAudit,
    RuntimeSupervisorLiveStatus,
)

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


def verify_runtime_live_evidence(
    request: RuntimeLiveEvidenceVerificationRequest,
) -> RuntimeLiveEvidenceVerificationResult:
    try:
        _validate_request(request)
        parents = RuntimeMinuteSupervisorStore(request.supervisor_store).records()
        children = RuntimeMinuteSupervisorStore(request.supervisor_store).live_records()
        paired = _paired_history(parents, children)
        inventory = load_runtime_live_evidence_inventory(
            request.manifest_root,
            request.receipt_root,
            request.actionability_store,
        )
        artifact_keys = tuple(_artifact_key(artifact) for artifact in inventory.artifacts)
        creation_artifacts = tuple(creation.artifact_id for creation in inventory.creations)
        if (
            len(artifact_keys) != len(set(artifact_keys))
            or len(creation_artifacts) != len(set(creation_artifacts))
            or not set(creation_artifacts).issubset({artifact.artifact_id for artifact in inventory.artifacts})
        ):
            raise RuntimeLiveEvidenceVerificationError
        completed = 0
        selected = 0
        created = 0
        replay = 0
        used_keys: set[_TerminalKey] = set()
        for parent, child in paired:
            if child.status is not RuntimeSupervisorLiveStatus.COMPLETED:
                continue
            current = tuple(
                manifest for manifest in inventory.manifests if manifest.snapshot.observed_at == parent.started_at
            )
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
    inventory: RuntimeLiveEvidenceInventory,
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
    bindings = tuple(creation for creation in inventory.creations if creation.artifact_id == artifact.artifact_id)
    if len(bindings) > 1:
        raise RuntimeLiveEvidenceVerificationError
    binding = bindings[0] if bindings else None
    source_manifests = tuple(source for source in inventory.manifests if _source_matches(source, artifact, binding))
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
    if binding is not None:
        source = source_manifests[0]
        if binding.evaluated_at != source.snapshot.observed_at:
            raise RuntimeLiveEvidenceVerificationError
        if binding.manifest_id == manifest.manifest_id:
            if parent.started_at <= terminal.terminal_at <= parent.finished_at:
                return True
            raise RuntimeLiveEvidenceVerificationError
        if binding.evaluated_at < parent.started_at:
            return False
        raise RuntimeLiveEvidenceVerificationError
    if parent.started_at <= terminal.terminal_at <= parent.finished_at:
        return True
    if terminal.terminal_at < parent.started_at:
        return False
    raise RuntimeLiveEvidenceVerificationError


def _source_matches(
    source: AlpacaSipQuoteActionabilityManifest,
    artifact: AlpacaSipQuoteActionabilityArtifact,
    binding: AlpacaSipQuoteActionabilityCreation | None,
) -> bool:
    trade = artifact.bundle.trade_confirmation
    return (
        _manifest_key(source) == _artifact_key(artifact)
        and source.base_publication == artifact.base_publication
        and source.plan.plan_id == trade.dynamic_plan_id
        and source.snapshot.identity.identity_sha256 == trade.research_input_identity_sha256
        and source.snapshot.instrument_id == trade.instrument_id
        and (binding is None or source.manifest_id == binding.manifest_id)
    )


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

from __future__ import annotations

import datetime as dt
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_sip_dynamic_plan_store import AlpacaSipDynamicPlanStore
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_live_actionability import (
    AlpacaSipLiveActionabilityConfig,
    AlpacaSipLiveActionabilityDependencies,
    AlpacaSipLiveActionabilityRequest,
    AlpacaSipLiveActionabilityStores,
    run_alpaca_sip_live_actionability,
)
from trading_agent.alpaca_sip_quote_actionability_manifest import (
    AlpacaSipQuoteActionabilityManifest,
    read_alpaca_sip_quote_actionability_manifest,
)
from trading_agent.alpaca_sip_quote_actionability_store import AlpacaSipQuoteActionabilityStore
from trading_agent.us_subscription_policy_state_store import SubscriptionPolicyStateStore

_MANIFEST_NAME = re.compile(r"^[0-9a-f]{64}\.json$", flags=re.ASCII)


class UsRuntimeLiveActionabilityDispatchError(ValueError):
    @override
    def __str__(self) -> str:
        return "US runtime live actionability dispatch is blocked"


@dataclass(frozen=True, slots=True)
class UsRuntimeLiveActionabilityDispatchRequest:
    manifest_root: Path
    evaluated_at: dt.datetime
    credentials: AlpacaCredentials
    plan_store: Path
    policy_state_store: Path
    receipt_root: Path
    actionability_store: Path
    config: AlpacaSipLiveActionabilityConfig


@dataclass(frozen=True, slots=True)
class UsRuntimeLiveActionabilityDispatchResult:
    selected_count: int
    created_count: int
    replay_count: int


def dispatch_us_runtime_live_actionability(
    request: UsRuntimeLiveActionabilityDispatchRequest,
    dependencies: AlpacaSipLiveActionabilityDependencies,
) -> UsRuntimeLiveActionabilityDispatchResult:
    try:
        _validate_request(request, dependencies)
        selected = _current_manifests(request.manifest_root, request.evaluated_at)
        if not selected:
            return UsRuntimeLiveActionabilityDispatchResult(0, 0, 0)
        actionability_store = AlpacaSipQuoteActionabilityStore(request.actionability_store)
        _ = actionability_store.creations()
        terminal_keys = tuple(
            (artifact.base_publication.signal.signal_id, artifact.assessment.scan_started_at)
            for artifact in actionability_store.records()
        )
        selected_keys = tuple(
            (manifest.base_publication.signal.signal_id, manifest.scan_started_at) for manifest in selected
        )
        terminal_key_set = set(terminal_keys)
        selected_key_set = set(selected_keys)
        if len(terminal_keys) != len(terminal_key_set) or len(selected_keys) != len(selected_key_set):
            raise UsRuntimeLiveActionabilityDispatchError
        pending = tuple(
            manifest for manifest, key in zip(selected, selected_keys, strict=True) if key not in terminal_key_set
        )
        if not pending:
            return UsRuntimeLiveActionabilityDispatchResult(len(selected), 0, len(selected))
        _prepare_receipt_root(request.receipt_root)
        created = 0
        for manifest in pending:
            result = run_alpaca_sip_live_actionability(
                AlpacaSipLiveActionabilityRequest(
                    request.credentials,
                    manifest,
                    AlpacaSipLiveActionabilityStores(
                        AlpacaSipDynamicPlanStore(request.plan_store),
                        SubscriptionPolicyStateStore(request.policy_state_store),
                        AlpacaSipDynamicReceiptStore(_receipt_path(request.receipt_root, manifest)),
                        actionability_store,
                    ),
                    request.config,
                ),
                dependencies,
            )
            created += result.projection.appended
        return UsRuntimeLiveActionabilityDispatchResult(
            len(selected),
            created,
            len(selected) - created,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        raise UsRuntimeLiveActionabilityDispatchError from None


def _validate_request(
    request: UsRuntimeLiveActionabilityDispatchRequest,
    dependencies: AlpacaSipLiveActionabilityDependencies,
) -> None:
    if (
        type(request) is not UsRuntimeLiveActionabilityDispatchRequest
        or type(dependencies) is not AlpacaSipLiveActionabilityDependencies
        or type(request.credentials) is not AlpacaCredentials
        or type(request.config) is not AlpacaSipLiveActionabilityConfig
        or not _aware(request.evaluated_at)
        or any(
            not isinstance(path, Path)
            for path in (
                request.manifest_root,
                request.plan_store,
                request.policy_state_store,
                request.receipt_root,
                request.actionability_store,
            )
        )
    ):
        raise UsRuntimeLiveActionabilityDispatchError


def _current_manifests(
    root: Path,
    evaluated_at: dt.datetime,
) -> tuple[AlpacaSipQuoteActionabilityManifest, ...]:
    source = root.expanduser().absolute()
    if not source.exists():
        if source.is_symlink():
            raise UsRuntimeLiveActionabilityDispatchError
        return ()
    metadata = source.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise UsRuntimeLiveActionabilityDispatchError
    manifests: list[AlpacaSipQuoteActionabilityManifest] = []
    for path in sorted(source.iterdir()):
        if _MANIFEST_NAME.fullmatch(path.name) is None:
            raise UsRuntimeLiveActionabilityDispatchError
        manifest = read_alpaca_sip_quote_actionability_manifest(path)
        if path.stem != _digest(manifest):
            raise UsRuntimeLiveActionabilityDispatchError
        if manifest.snapshot.observed_at == evaluated_at:
            manifests.append(manifest)
    selected = tuple(sorted(manifests, key=lambda item: item.snapshot.instrument_id))
    instruments = tuple(item.snapshot.instrument_id for item in selected)
    if len(instruments) != len(set(instruments)):
        raise UsRuntimeLiveActionabilityDispatchError
    return selected


def _prepare_receipt_root(root: Path) -> None:
    destination = root.expanduser().absolute()
    if destination.is_symlink():
        raise UsRuntimeLiveActionabilityDispatchError
    if not destination.exists():
        destination.mkdir(parents=True, mode=0o700)
    metadata = destination.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise UsRuntimeLiveActionabilityDispatchError


def _receipt_path(
    root: Path,
    manifest: AlpacaSipQuoteActionabilityManifest,
) -> Path:
    return root.expanduser().absolute() / f"{_digest(manifest)}.sqlite3"


def _digest(manifest: AlpacaSipQuoteActionabilityManifest) -> str:
    return manifest.manifest_id.rpartition(":")[2]


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "UsRuntimeLiveActionabilityDispatchError",
    "UsRuntimeLiveActionabilityDispatchRequest",
    "UsRuntimeLiveActionabilityDispatchResult",
    "dispatch_us_runtime_live_actionability",
)

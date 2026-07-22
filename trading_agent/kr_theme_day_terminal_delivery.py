from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import override

from pydantic import BaseModel

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.hermes_delivery_errors import (
    HermesDeliveryConflictError,
    HermesDeliveryWriterLeaseUnavailableError,
    InvalidHermesDeliveryStoreError,
)
from trading_agent.hermes_delivery_models import (
    HERMES_DELIVERY_CONTRACT_VERSION,
    hermes_delivery_id,
)
from trading_agent.hermes_delivery_projection import (
    HermesProjectionRecord,
    HermesProjectionResult,
    InvalidHermesProjectionSourceError,
    project_outcomes,
)
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_theme_day_shadow_entry_models import KrThemeDayShadowEntry
from trading_agent.kr_theme_day_shadow_entry_store import (
    InvalidKrThemeDayShadowEntryStoreError,
    KrThemeDayShadowEntryStore,
)
from trading_agent.kr_theme_day_shadow_exit_models import KrThemeDayShadowExit
from trading_agent.kr_theme_day_shadow_exit_store import (
    InvalidKrThemeDayShadowExitStoreError,
    KrThemeDayShadowExitStore,
)
from trading_agent.kr_theme_day_terminal_delivery_records import (
    build_kr_theme_day_terminal_delivery_records,
)
from trading_agent.kr_theme_day_trial_terminal_models import KrThemeDayTrialTerminalArtifact
from trading_agent.kr_theme_day_trial_terminal_store import (
    InvalidKrThemeDayTrialTerminalStoreError,
    KrThemeDayTrialTerminalStore,
)


class InvalidKrThemeDayTerminalDeliveryError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day terminal delivery source is invalid"


@dataclass(frozen=True, slots=True)
class KrThemeDayTerminalDeliverySources:
    entry_store: KrThemeDayShadowEntryStore
    exit_store: KrThemeDayShadowExitStore
    terminal_store: KrThemeDayTrialTerminalStore
    delivery_store: HermesDeliveryStore


def project_kr_theme_day_terminal_delivery(
    sources: KrThemeDayTerminalDeliverySources,
    trial_id: str,
) -> HermesProjectionResult:
    try:
        artifact = _terminal_artifact(sources, trial_id)
        entries = _trial_entries(sources, artifact)
        exits = _trial_exits(sources, artifact)
        records = build_kr_theme_day_terminal_delivery_records(artifact, entries, exits)
        _require_reply_roots(sources.delivery_store, records)
        with sources.delivery_store.writer() as writer:
            return project_outcomes(records, writer)
    except (
        AttributeError,
        HermesDeliveryConflictError,
        HermesDeliveryWriterLeaseUnavailableError,
        InvalidHermesDeliveryStoreError,
        InvalidHermesProjectionSourceError,
        InvalidKrThemeDayShadowEntryStoreError,
        InvalidKrThemeDayShadowExitStoreError,
        InvalidKrThemeDayTrialTerminalStoreError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise InvalidKrThemeDayTerminalDeliveryError from None


def _terminal_artifact(
    sources: KrThemeDayTerminalDeliverySources,
    trial_id: str,
) -> KrThemeDayTrialTerminalArtifact:
    if not trial_id or trial_id != trial_id.strip():
        raise InvalidKrThemeDayTerminalDeliveryError
    matches = tuple(item for item in sources.terminal_store.artifacts() if item.payload.trial_id == trial_id)
    if len(matches) != 1:
        raise InvalidKrThemeDayTerminalDeliveryError
    return matches[0]


def _trial_entries(
    sources: KrThemeDayTerminalDeliverySources,
    artifact: KrThemeDayTrialTerminalArtifact,
) -> tuple[KrThemeDayShadowEntry, ...]:
    entries = tuple(
        sorted(
            (item for item in sources.entry_store.entries() if item.trial_id == artifact.payload.trial_id),
            key=lambda item: item.entry_id,
        )
    )
    if (
        tuple(item.entry_id for item in entries) != artifact.payload.entry_ids
        or tuple(_payload_sha256(item) for item in entries) != artifact.payload.entry_payload_sha256s
    ):
        raise InvalidKrThemeDayTerminalDeliveryError
    return entries


def _trial_exits(
    sources: KrThemeDayTerminalDeliverySources,
    artifact: KrThemeDayTrialTerminalArtifact,
) -> tuple[KrThemeDayShadowExit, ...]:
    exits = tuple(
        sorted(
            (item for item in sources.exit_store.exits() if item.trial_id == artifact.payload.trial_id),
            key=lambda item: item.exit_id,
        )
    )
    if (
        tuple(item.exit_id for item in exits) != artifact.payload.exit_ids
        or tuple(_payload_sha256(item) for item in exits) != artifact.payload.exit_payload_sha256s
    ):
        raise InvalidKrThemeDayTerminalDeliveryError
    return exits


def _require_reply_roots(
    store: HermesDeliveryStore,
    records: tuple[HermesProjectionRecord, ...],
) -> None:
    existing = frozenset(item.delivery_id for item in store.events())
    expected = tuple(
        hermes_delivery_id(item.root_source_event_id, HERMES_DELIVERY_CONTRACT_VERSION)
        for item in records
        if item.root_source_event_id is not None
    )
    if any(item not in existing for item in expected):
        raise InvalidKrThemeDayTerminalDeliveryError


def _payload_sha256(value: BaseModel) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(value).encode()).hexdigest()


__all__ = (
    "InvalidKrThemeDayTerminalDeliveryError",
    "KrThemeDayTerminalDeliverySources",
    "project_kr_theme_day_terminal_delivery",
)

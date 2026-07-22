from __future__ import annotations

import datetime as dt
from typing import assert_never
from zoneinfo import ZoneInfo

from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import InvalidExperimentLedgerSourceError
from trading_agent.hermes_delivery_errors import InvalidHermesDeliveryStoreError
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kis_kr_market_models import KisKrMarketReceipt, KisKrMarketReceiptKind
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kr_theme_day_post_session_source_state import kr_theme_day_post_session_references
from trading_agent.kr_theme_day_session_audit import KrThemeDaySessionPhase
from trading_agent.kr_theme_day_session_evidence import (
    InvalidKrThemeDaySessionEvidenceError,
    KrThemeDaySessionSourceState,
    kr_theme_day_session_source_state,
)
from trading_agent.kr_theme_day_session_manifest import KrThemeDaySessionManifest
from trading_agent.kr_theme_day_shadow_entry_store import KrThemeDayShadowEntryStore
from trading_agent.kr_theme_day_shadow_exit_store import KrThemeDayShadowExitStore
from trading_agent.kr_theme_day_trial import (
    kr_theme_day_trial_id,
    require_exact_kr_theme_day_trial,
)
from trading_agent.private_experiment_ledger_snapshot import open_private_experiment_ledger_snapshot

KST = ZoneInfo("Asia/Seoul")


def resolve_kr_theme_day_session_source_state(
    manifest: KrThemeDaySessionManifest,
    phase: KrThemeDaySessionPhase,
    cycle_key: str,
) -> KrThemeDaySessionSourceState:
    return _resolve_source_state(manifest, phase, cycle_key, None)


def resolve_kr_theme_day_session_source_state_at(
    manifest: KrThemeDaySessionManifest,
    phase: KrThemeDaySessionPhase,
    cycle_key: str,
    observed_through: dt.datetime,
) -> KrThemeDaySessionSourceState:
    if observed_through.tzinfo is None or observed_through.utcoffset() is None:
        raise InvalidKrThemeDaySessionEvidenceError
    return _resolve_source_state(manifest, phase, cycle_key, observed_through)


def _resolve_source_state(
    manifest: KrThemeDaySessionManifest,
    phase: KrThemeDaySessionPhase,
    cycle_key: str,
    observed_through: dt.datetime | None,
) -> KrThemeDaySessionSourceState:
    try:
        trial_refs = _trial_references(manifest, require_started=phase is not KrThemeDaySessionPhase.REGISTER)
        match phase:
            case KrThemeDaySessionPhase.REGISTER | KrThemeDaySessionPhase.START:
                references = trial_refs
            case KrThemeDaySessionPhase.INTRADAY_COLLECT:
                references = (*trial_refs, *_receipt_references(manifest, phase, cycle_key, observed_through))
            case KrThemeDaySessionPhase.INTRADAY_ENTRY:
                cutoff = _phase_cutoff(manifest, phase, cycle_key)
                references = (
                    *trial_refs,
                    *_receipt_references(manifest, phase, cycle_key, observed_through),
                    *_entry_references(manifest, cutoff, observed_through),
                )
            case KrThemeDaySessionPhase.INTRADAY_EXIT:
                cutoff = _phase_cutoff(manifest, phase, cycle_key)
                references = (
                    *trial_refs,
                    *_receipt_references(manifest, phase, cycle_key, observed_through),
                    *_entry_references(manifest, cutoff, observed_through),
                    *_exit_references(manifest, cutoff, observed_through),
                )
            case KrThemeDaySessionPhase.EOD_COLLECT:
                references = (*trial_refs, *_receipt_references(manifest, phase, cycle_key, observed_through))
            case KrThemeDaySessionPhase.EOD_EXIT:
                cutoff = _phase_cutoff(manifest, phase, cycle_key)
                references = (
                    *trial_refs,
                    *_receipt_references(manifest, phase, cycle_key, observed_through),
                    *_entry_references(manifest, cutoff, observed_through),
                    *_exit_references(manifest, cutoff, observed_through),
                )
            case KrThemeDaySessionPhase.POST_SESSION:
                references = (*trial_refs, *kr_theme_day_post_session_references(manifest))
            case unreachable:
                assert_never(unreachable)
        return kr_theme_day_session_source_state(tuple(sorted(references)))
    except (
        AttributeError,
        InvalidExperimentLedgerSourceError,
        InvalidHermesDeliveryStoreError,
        TypeError,
        ValueError,
    ):
        raise InvalidKrThemeDaySessionEvidenceError from None


def _trial_references(
    manifest: KrThemeDaySessionManifest,
    *,
    require_started: bool,
) -> tuple[str, ...]:
    trial_id = kr_theme_day_trial_id(manifest.session_date, manifest.strategy_version)
    with open_private_experiment_ledger_snapshot(manifest.paths.experiment_ledger) as ledger:
        trials = tuple(item for item in ledger.multi_market_trials() if item.registration.trial_id == trial_id)
        if len(trials) != 1:
            raise InvalidKrThemeDaySessionEvidenceError
        require_exact_kr_theme_day_trial(ledger, trials[0].registration)
        references = [f"trial:{trials[0].registration_key}"]
        events = ledger.multi_market_trial_events(trial_id)
    if require_started:
        started = tuple(event for event in events if event.event.event_kind is TrialEventKind.STARTED)
        if len(started) != 1:
            raise InvalidKrThemeDaySessionEvidenceError
        references.append(f"started:{started[0].event_key}")
    return tuple(references)


def _receipt_references(
    manifest: KrThemeDaySessionManifest,
    phase: KrThemeDaySessionPhase,
    cycle_key: str,
    observed_through: dt.datetime | None,
) -> tuple[str, ...]:
    receipts = tuple(
        receipt
        for receipt in KisKrMarketReceiptStore(manifest.paths.receipt_store).receipts()
        if receipt.symbol == manifest.symbol
        and _receipt_in_cycle(receipt, phase, cycle_key)
        and (observed_through is None or receipt.received_at <= observed_through)
    )
    expected = (
        {KisKrMarketReceiptKind.MINUTE_BARS}
        if phase in {KrThemeDaySessionPhase.EOD_COLLECT, KrThemeDaySessionPhase.EOD_EXIT}
        else set(KisKrMarketReceiptKind)
    )
    latest = tuple(
        max(
            (item for item in receipts if item.kind is kind),
            key=lambda item: item.received_at,
        )
        for kind in expected
    )
    if {item.kind for item in latest} != expected:
        raise InvalidKrThemeDaySessionEvidenceError
    return tuple(
        f"receipt:{item.kind.value}:{item.received_at.isoformat()}:{item.payload_sha256}"
        for item in sorted(latest, key=lambda item: item.kind.value)
    )


def _receipt_in_cycle(
    receipt: KisKrMarketReceipt,
    phase: KrThemeDaySessionPhase,
    cycle_key: str,
) -> bool:
    local = receipt.received_at.astimezone(KST)
    if phase in {KrThemeDaySessionPhase.EOD_COLLECT, KrThemeDaySessionPhase.EOD_EXIT}:
        return dt.time(15, 30) <= local.time() < dt.time(15, 31)
    cycle = dt.datetime.fromisoformat(cycle_key).astimezone(KST)
    return local.replace(second=0, microsecond=0) == cycle.replace(second=0, microsecond=0)


def _entry_references(
    manifest: KrThemeDaySessionManifest,
    cutoff: dt.datetime,
    observed_through: dt.datetime | None,
) -> tuple[str, ...]:
    trial_id = kr_theme_day_trial_id(manifest.session_date, manifest.strategy_version)
    bounded = cutoff if observed_through is None else min(cutoff, observed_through)
    entries = tuple(
        sorted(
            (
                entry
                for entry in KrThemeDayShadowEntryStore(manifest.paths.entry_store).entries()
                if entry.trial_id == trial_id and entry.filled_at.astimezone(KST) <= bounded
            ),
            key=lambda entry: entry.entry_id,
        )
    )
    delivery_events = HermesDeliveryStore(manifest.paths.delivery_store).events() if entries else ()
    delivery_ids: list[str] = []
    for entry in entries:
        matches = tuple(
            event
            for event in delivery_events
            if event.source_event_id == entry.signal_id
            and event.kind is HermesDeliveryKind.ACTIONABLE
            and event.market_id == "kr_equities"
            and event.agent_family == "day_trading"
            and event.instrument_id == entry.symbol
            and event.strategy_version == entry.strategy_version
            and event.occurred_at == entry.signal_observed_at
            and event.status == "current_quote_validated"
            and event.root_delivery_id != event.delivery_id
        )
        if len(matches) != 1:
            raise InvalidKrThemeDaySessionEvidenceError
        delivery_ids.append(matches[0].delivery_id)
    return (
        f"entry-count:{len(entries)}",
        *(f"entry:{entry.entry_id}" for entry in entries),
        f"delivery-count:{len(delivery_ids)}",
        *(f"delivery:{delivery_id}" for delivery_id in sorted(delivery_ids)),
    )


def _exit_references(
    manifest: KrThemeDaySessionManifest,
    cutoff: dt.datetime,
    observed_through: dt.datetime | None,
) -> tuple[str, ...]:
    trial_id = kr_theme_day_trial_id(manifest.session_date, manifest.strategy_version)
    bounded = cutoff if observed_through is None else min(cutoff, observed_through)
    ids = tuple(
        sorted(
            exit.exit_id
            for exit in KrThemeDayShadowExitStore(manifest.paths.exit_store).exits()
            if exit.trial_id == trial_id and exit.evaluated_at.astimezone(KST) <= bounded
        )
    )
    return (f"exit-count:{len(ids)}", *(f"exit:{value}" for value in ids))


def _phase_cutoff(
    manifest: KrThemeDaySessionManifest,
    phase: KrThemeDaySessionPhase,
    cycle_key: str,
) -> dt.datetime:
    if phase is KrThemeDaySessionPhase.EOD_EXIT:
        return dt.datetime.combine(manifest.session_date, dt.time(15, 31), tzinfo=KST)
    cycle = dt.datetime.fromisoformat(cycle_key).astimezone(KST)
    return cycle.replace(second=59, microsecond=999_999)

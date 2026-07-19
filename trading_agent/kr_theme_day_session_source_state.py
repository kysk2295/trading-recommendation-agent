from __future__ import annotations

import datetime as dt
from typing import assert_never
from zoneinfo import ZoneInfo

from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.kis_kr_market_models import KisKrMarketReceipt, KisKrMarketReceiptKind
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kr_theme_day_review_store import (
    KrThemeDayReviewStore,
    kr_theme_day_review_event_key,
)
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
from trading_agent.kr_theme_day_trial_terminal_store import KrThemeDayTrialTerminalStore

KST = ZoneInfo("Asia/Seoul")


def resolve_kr_theme_day_session_source_state(
    manifest: KrThemeDaySessionManifest,
    phase: KrThemeDaySessionPhase,
    cycle_key: str,
) -> KrThemeDaySessionSourceState:
    try:
        trial_refs = _trial_references(manifest, require_started=phase is not KrThemeDaySessionPhase.REGISTER)
        match phase:
            case KrThemeDaySessionPhase.REGISTER | KrThemeDaySessionPhase.START:
                references = trial_refs
            case KrThemeDaySessionPhase.INTRADAY_COLLECT:
                references = (*trial_refs, *_receipt_references(manifest, phase, cycle_key))
            case KrThemeDaySessionPhase.INTRADAY_ENTRY:
                cutoff = _phase_cutoff(manifest, phase, cycle_key)
                references = (
                    *trial_refs,
                    *_receipt_references(manifest, phase, cycle_key),
                    *_entry_references(manifest, cutoff),
                )
            case KrThemeDaySessionPhase.INTRADAY_EXIT:
                cutoff = _phase_cutoff(manifest, phase, cycle_key)
                references = (
                    *trial_refs,
                    *_receipt_references(manifest, phase, cycle_key),
                    *_entry_references(manifest, cutoff),
                    *_exit_references(manifest, cutoff),
                )
            case KrThemeDaySessionPhase.EOD_COLLECT:
                references = (*trial_refs, *_receipt_references(manifest, phase, cycle_key))
            case KrThemeDaySessionPhase.EOD_EXIT:
                cutoff = _phase_cutoff(manifest, phase, cycle_key)
                references = (
                    *trial_refs,
                    *_receipt_references(manifest, phase, cycle_key),
                    *_entry_references(manifest, cutoff),
                    *_exit_references(manifest, cutoff),
                )
            case KrThemeDaySessionPhase.POST_SESSION:
                references = (*trial_refs, *_post_session_references(manifest))
            case unreachable:
                assert_never(unreachable)
        return kr_theme_day_session_source_state(tuple(sorted(references)))
    except (AttributeError, TypeError, ValueError):
        raise InvalidKrThemeDaySessionEvidenceError from None


def _trial_references(
    manifest: KrThemeDaySessionManifest,
    *,
    require_started: bool,
) -> tuple[str, ...]:
    ledger = ExperimentLedgerReader(manifest.paths.experiment_ledger)
    trial_id = kr_theme_day_trial_id(manifest.session_date, manifest.strategy_version)
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
) -> tuple[str, ...]:
    receipts = tuple(
        receipt
        for receipt in KisKrMarketReceiptStore(manifest.paths.receipt_store).receipts()
        if receipt.symbol == manifest.symbol and _receipt_in_cycle(receipt, phase, cycle_key)
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
) -> tuple[str, ...]:
    trial_id = kr_theme_day_trial_id(manifest.session_date, manifest.strategy_version)
    ids = tuple(
        sorted(
            entry.entry_id
            for entry in KrThemeDayShadowEntryStore(manifest.paths.entry_store).entries()
            if entry.trial_id == trial_id and entry.filled_at.astimezone(KST) <= cutoff
        )
    )
    return (f"entry-count:{len(ids)}", *(f"entry:{value}" for value in ids))


def _exit_references(
    manifest: KrThemeDaySessionManifest,
    cutoff: dt.datetime,
) -> tuple[str, ...]:
    trial_id = kr_theme_day_trial_id(manifest.session_date, manifest.strategy_version)
    ids = tuple(
        sorted(
            exit.exit_id
            for exit in KrThemeDayShadowExitStore(manifest.paths.exit_store).exits()
            if exit.trial_id == trial_id and exit.evaluated_at.astimezone(KST) <= cutoff
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


def _post_session_references(manifest: KrThemeDaySessionManifest) -> tuple[str, ...]:
    trial_id = kr_theme_day_trial_id(manifest.session_date, manifest.strategy_version)
    ledger = ExperimentLedgerReader(manifest.paths.experiment_ledger)
    events = ledger.multi_market_trial_events(trial_id)
    artifacts = tuple(
        item
        for item in KrThemeDayTrialTerminalStore(manifest.paths.terminal_store).artifacts()
        if item.payload.trial_id == trial_id
    )
    reviews = tuple(
        event
        for event in KrThemeDayReviewStore(manifest.paths.review_store).events()
        if event.strategy_version == manifest.strategy_version and event.as_of_session == manifest.session_date
    )
    lifecycle = tuple(
        item
        for item in ledger.multi_market_lifecycle_events(manifest.strategy_version)
        if item.event.decision_session_date == manifest.session_date
    )
    if len(events) != 2 or len(artifacts) != 1 or len(reviews) != 1 or not lifecycle:
        raise InvalidKrThemeDaySessionEvidenceError
    return (
        *(f"trial-event:{item.event_key}" for item in events),
        f"terminal:{artifacts[0].artifact_id}",
        f"review:{kr_theme_day_review_event_key(reviews[0])}",
        *(f"lifecycle:{item.event_key}" for item in lifecycle),
    )

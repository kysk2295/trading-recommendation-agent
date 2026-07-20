from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from tests.test_kis_kr_market_projection import (
    _minute_body,
    _price_body,
    _quote_body,
    _receipt,
)
from tests.test_kr_theme_day_onboarding import _prepared_request
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kis_kr_market_models import KisKrMarketReceiptKind
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kr_theme_day_onboarding import onboard_kr_theme_day_opportunity
from trading_agent.kr_theme_day_session_audit import (
    KrThemeDaySessionPhase,
    KrThemeDaySessionPhaseEvent,
    KrThemeDaySessionPhaseEventRequest,
    KrThemeDaySessionPhaseStatus,
    build_kr_theme_day_session_phase_event,
)
from trading_agent.kr_theme_day_session_audit_store import KrThemeDaySessionAuditStore
from trading_agent.kr_theme_day_session_evidence import (
    KrThemeDaySessionSourceAttestation,
    build_kr_theme_day_session_source_attestation,
)
from trading_agent.kr_theme_day_session_evidence_store import KrThemeDaySessionEvidenceStore
from trading_agent.kr_theme_day_session_manifest import KrThemeDaySessionManifest
from trading_agent.kr_theme_day_session_source_state import resolve_kr_theme_day_session_source_state
from trading_agent.kr_theme_day_session_verifier import (
    KrThemeDaySessionVerificationResult,
    verify_kr_theme_day_session,
)
from trading_agent.kr_theme_day_trial import start_kr_theme_day_shadow_trial

KST = ZoneInfo("Asia/Seoul")
VERIFIED_AT = dt.datetime(2026, 7, 20, 9, 4, 10, tzinfo=KST)
CYCLE_KEY = "2026-07-20T09:04+09:00"


def production_session(
    tmp_path: Path,
    *,
    receipt_seconds: tuple[int, int, int] = (1, 2, 3),
    entry_store: Path | None = None,
) -> tuple[
    KrThemeDaySessionManifest,
    KrThemeDaySessionVerificationResult,
    tuple[KrThemeDaySessionPhaseEvent, ...],
    tuple[KrThemeDaySessionSourceAttestation, ...],
]:
    request = _prepared_request(tmp_path)
    if entry_store is not None:
        request = request.model_copy(
            update={"paths": request.paths.model_copy(update={"entry_store": entry_store})},
        )
    manifest = onboard_kr_theme_day_opportunity(request).manifest
    _ = start_kr_theme_day_shadow_trial(
        ExperimentLedgerStore(manifest.paths.experiment_ledger),
        request.trial_id,
        dt.datetime(2026, 7, 20, 9, tzinfo=KST),
    )
    receipt_store = KisKrMarketReceiptStore(manifest.paths.receipt_store)
    receipts = (
        _receipt(KisKrMarketReceiptKind.MINUTE_BARS, _minute_body(), seconds=receipt_seconds[0]),
        _receipt(KisKrMarketReceiptKind.PRICE_STATUS, _price_body(), seconds=receipt_seconds[1]),
        _receipt(KisKrMarketReceiptKind.ORDER_BOOK, _quote_body(), seconds=receipt_seconds[2]),
    )
    assert all(receipt_store.append(receipt) for receipt in receipts)
    events, attestations = _append_phase_evidence(manifest)
    return manifest, verify_kr_theme_day_session(manifest), events, attestations


def _append_phase_evidence(
    manifest: KrThemeDaySessionManifest,
) -> tuple[tuple[KrThemeDaySessionPhaseEvent, ...], tuple[KrThemeDaySessionSourceAttestation, ...]]:
    requests = (
        (KrThemeDaySessionPhase.REGISTER, "session", VERIFIED_AT.replace(hour=8, minute=40)),
        (KrThemeDaySessionPhase.START, "session", VERIFIED_AT.replace(hour=9, minute=0)),
        (KrThemeDaySessionPhase.INTRADAY_COLLECT, CYCLE_KEY, VERIFIED_AT.replace(second=4)),
        (KrThemeDaySessionPhase.INTRADAY_ENTRY, CYCLE_KEY, VERIFIED_AT.replace(second=5)),
        (KrThemeDaySessionPhase.INTRADAY_EXIT, CYCLE_KEY, VERIFIED_AT.replace(second=6)),
    )
    audit = KrThemeDaySessionAuditStore(manifest.paths.audit_store)
    evidence_store = KrThemeDaySessionEvidenceStore(manifest.paths.audit_store)
    events: list[KrThemeDaySessionPhaseEvent] = []
    attestations: list[KrThemeDaySessionSourceAttestation] = []
    for sequence, (phase, cycle, observed_at) in enumerate(requests, start=1):
        event = build_kr_theme_day_session_phase_event(
            KrThemeDaySessionPhaseEventRequest(
                manifest.session_id,
                phase,
                cycle,
                observed_at,
                KrThemeDaySessionPhaseStatus.COMPLETED,
                0,
            ),
            sequence,
            None if not events else events[-1].event_id,
        )
        assert audit.append(event) is True
        attestation = build_kr_theme_day_session_source_attestation(
            event,
            resolve_kr_theme_day_session_source_state(manifest, phase, cycle),
        )
        assert evidence_store.append(attestation) is True
        events.append(event)
        attestations.append(attestation)
    return tuple(events), tuple(attestations)

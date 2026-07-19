from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from tests.kr_theme_day_open_smoke_support import (
    CYCLE_KEY,
    VERIFIED_AT,
    production_session,
)
from tests.test_kis_kr_market_projection import _quote_body, _receipt
from tests.test_kr_theme_day_session_e2e import _manifest as fixture_manifest
from trading_agent.kis_kr_market_models import KisKrMarketReceiptKind
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kr_theme_day_open_smoke import (
    InvalidKrThemeDayOpenSmokeError,
    attest_kr_theme_day_open_smoke,
    load_kr_theme_day_open_smoke,
    publish_kr_theme_day_open_smoke,
)
from trading_agent.kr_theme_day_session_audit import (
    KrThemeDaySessionPhase,
    KrThemeDaySessionPhaseEventRequest,
    KrThemeDaySessionPhaseStatus,
    build_kr_theme_day_session_phase_event,
)
from trading_agent.kr_theme_day_session_evidence import (
    build_kr_theme_day_session_source_attestation,
    kr_theme_day_session_source_state,
)
from trading_agent.kr_theme_day_session_source_state import resolve_kr_theme_day_session_source_state
from trading_agent.kr_theme_day_session_verifier import KrThemeDaySessionVerificationResult


def test_open_smoke_attests_current_production_cycle_and_replays(tmp_path: Path) -> None:
    # Given
    manifest, verification, events, attestations = production_session(tmp_path)

    # When
    evidence = attest_kr_theme_day_open_smoke(manifest, verification, events, attestations, VERIFIED_AT)
    path = tmp_path / "open-smoke.json"
    first = publish_kr_theme_day_open_smoke(path, evidence)
    replay = publish_kr_theme_day_open_smoke(path, evidence)

    # Then
    assert (first, replay) == (True, False)
    assert load_kr_theme_day_open_smoke(path) == evidence
    assert evidence.cycle_key == CYCLE_KEY
    assert len(evidence.phase_event_ids) == 3
    assert len(evidence.source_attestation_ids) == 3
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_open_smoke_rejects_fixture_closed_time_and_incomplete_cycle(tmp_path: Path) -> None:
    # Given
    manifest, verification, events, attestations = production_session(tmp_path / "production")
    fixture = fixture_manifest(tmp_path / "fixture")

    # When / Then
    with pytest.raises(InvalidKrThemeDayOpenSmokeError):
        _ = attest_kr_theme_day_open_smoke(fixture, verification, events, attestations, VERIFIED_AT)
    with pytest.raises(InvalidKrThemeDayOpenSmokeError):
        _ = attest_kr_theme_day_open_smoke(
            manifest,
            verification,
            events,
            attestations,
            VERIFIED_AT.replace(hour=15, minute=30),
        )
    with pytest.raises(InvalidKrThemeDayOpenSmokeError):
        incomplete_verification = KrThemeDaySessionVerificationResult(
            event_count=verification.event_count - 1,
            completed_count=verification.completed_count - 1,
            blocked_count=0,
            attested_count=verification.attested_count - 1,
        )
        _ = attest_kr_theme_day_open_smoke(
            manifest,
            incomplete_verification,
            events[:-1],
            attestations[:-1],
            VERIFIED_AT,
        )


def test_open_smoke_rejects_tampered_artifact(tmp_path: Path) -> None:
    # Given
    manifest, verification, events, attestations = production_session(tmp_path)
    evidence = attest_kr_theme_day_open_smoke(manifest, verification, events, attestations, VERIFIED_AT)
    path = tmp_path / "open-smoke.json"
    assert publish_kr_theme_day_open_smoke(path, evidence) is True

    # When / Then
    path.write_text(path.read_text(encoding="utf-8").replace(CYCLE_KEY, "2026-07-20T09:05+09:00"), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(InvalidKrThemeDayOpenSmokeError):
        _ = load_kr_theme_day_open_smoke(path)


def test_open_smoke_rejects_rehashed_naive_cycle(tmp_path: Path) -> None:
    # Given
    manifest, verification, events, attestations = production_session(tmp_path)
    evidence = attest_kr_theme_day_open_smoke(manifest, verification, events, attestations, VERIFIED_AT)
    payload = evidence.model_dump(mode="json") | {"cycle_key": "2026-07-20T09:04:00"}
    encoded = json.dumps(
        {key: value for key, value in payload.items() if key != "evidence_id"},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    payload["evidence_id"] = hashlib.sha256(encoded).hexdigest()
    path = tmp_path / "naive-cycle.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)

    # When / Then
    with pytest.raises(InvalidKrThemeDayOpenSmokeError):
        _ = load_kr_theme_day_open_smoke(path)


def test_open_smoke_rejects_event_observed_outside_current_cycle(tmp_path: Path) -> None:
    # Given
    manifest, verification, events, attestations = production_session(tmp_path)
    current = next(event for event in events if event.phase is KrThemeDaySessionPhase.INTRADAY_COLLECT)
    replacement = build_kr_theme_day_session_phase_event(
        KrThemeDaySessionPhaseEventRequest(
            current.session_id,
            current.phase,
            current.cycle_key,
            VERIFIED_AT.replace(hour=9, minute=0, second=30),
            current.status,
            current.exit_code,
        ),
        current.sequence,
        current.previous_event_id,
    )
    replacement_attestation = build_kr_theme_day_session_source_attestation(
        replacement,
        resolve_kr_theme_day_session_source_state(manifest, replacement.phase, replacement.cycle_key),
    )
    changed_events = tuple(replacement if item.event_id == current.event_id else item for item in events)
    changed_attestations = tuple(
        replacement_attestation if item.event_id == current.event_id else item for item in attestations
    )

    # When / Then
    with pytest.raises(InvalidKrThemeDayOpenSmokeError):
        _ = attest_kr_theme_day_open_smoke(
            manifest,
            verification,
            changed_events,
            changed_attestations,
            VERIFIED_AT,
        )


def test_open_smoke_rejects_future_current_cycle_source(tmp_path: Path) -> None:
    # Given
    manifest, verification, events, attestations = production_session(
        tmp_path,
        receipt_seconds=(1, 2, 20),
    )

    # When / Then
    with pytest.raises(InvalidKrThemeDayOpenSmokeError):
        _ = attest_kr_theme_day_open_smoke(manifest, verification, events, attestations, VERIFIED_AT)


def test_open_smoke_rejects_source_drift_after_session_verification(tmp_path: Path) -> None:
    # Given
    manifest, verification, events, attestations = production_session(tmp_path)
    store = KisKrMarketReceiptStore(manifest.paths.receipt_store)
    assert store.append(_receipt(KisKrMarketReceiptKind.ORDER_BOOK, _quote_body(), seconds=8)) is True

    # When / Then
    with pytest.raises(InvalidKrThemeDayOpenSmokeError):
        _ = attest_kr_theme_day_open_smoke(manifest, verification, events, attestations, VERIFIED_AT)


def test_open_smoke_rejects_later_completed_event_from_other_cycle(tmp_path: Path) -> None:
    # Given
    manifest, verification, events, attestations = production_session(tmp_path)
    future = build_kr_theme_day_session_phase_event(
        KrThemeDaySessionPhaseEventRequest(
            manifest.session_id,
            KrThemeDaySessionPhase.INTRADAY_COLLECT,
            "2026-07-20T09:05+09:00",
            VERIFIED_AT.replace(minute=5, second=6),
            KrThemeDaySessionPhaseStatus.COMPLETED,
            0,
        ),
        events[-1].sequence + 1,
        events[-1].event_id,
    )
    future_attestation = build_kr_theme_day_session_source_attestation(
        future,
        kr_theme_day_session_source_state(("future-cycle",)),
    )
    expanded_verification = KrThemeDaySessionVerificationResult(
        event_count=verification.event_count + 1,
        completed_count=verification.completed_count + 1,
        blocked_count=0,
        attested_count=verification.attested_count + 1,
    )

    # When / Then
    with pytest.raises(InvalidKrThemeDayOpenSmokeError):
        _ = attest_kr_theme_day_open_smoke(
            manifest,
            expanded_verification,
            (*events, future),
            (*attestations, future_attestation),
            VERIFIED_AT,
        )


def test_open_smoke_rejects_forged_event_content_address(tmp_path: Path) -> None:
    # Given
    manifest, verification, events, attestations = production_session(tmp_path)
    current = events[-1]
    forged = replace(current, observed_at=current.observed_at.replace(second=7))
    changed = tuple(forged if item.event_id == current.event_id else item for item in events)

    # When / Then
    with pytest.raises(InvalidKrThemeDayOpenSmokeError):
        _ = attest_kr_theme_day_open_smoke(manifest, verification, changed, attestations, VERIFIED_AT)


def test_open_smoke_rejects_forged_attestation_content_address(tmp_path: Path) -> None:
    # Given
    manifest, verification, events, attestations = production_session(tmp_path)
    current = attestations[-1]
    forged = replace(current, attestation_id="f" * 64)
    changed = tuple(forged if item.event_id == current.event_id else item for item in attestations)

    # When / Then
    with pytest.raises(InvalidKrThemeDayOpenSmokeError):
        _ = attest_kr_theme_day_open_smoke(manifest, verification, events, changed, VERIFIED_AT)


def test_open_smoke_rejects_missing_register_start_prefix(tmp_path: Path) -> None:
    # Given
    manifest, _verification, events, attestations = production_session(tmp_path)
    incomplete = KrThemeDaySessionVerificationResult(
        event_count=3,
        completed_count=3,
        blocked_count=0,
        attested_count=3,
    )

    # When / Then
    with pytest.raises(InvalidKrThemeDayOpenSmokeError):
        _ = attest_kr_theme_day_open_smoke(manifest, incomplete, events[2:], attestations[2:], VERIFIED_AT)


def test_open_smoke_rejects_future_eod_event_outside_open_history(tmp_path: Path) -> None:
    # Given
    manifest, verification, events, attestations = production_session(tmp_path)
    future = build_kr_theme_day_session_phase_event(
        KrThemeDaySessionPhaseEventRequest(
            manifest.session_id,
            KrThemeDaySessionPhase.EOD_COLLECT,
            "eod",
            VERIFIED_AT.replace(hour=15, minute=30),
            KrThemeDaySessionPhaseStatus.COMPLETED,
            0,
        ),
        events[2].sequence,
        events[1].event_id,
    )
    future_attestation = build_kr_theme_day_session_source_attestation(
        future,
        kr_theme_day_session_source_state(("future-eod",)),
    )
    expanded = KrThemeDaySessionVerificationResult(
        event_count=verification.event_count + 1,
        completed_count=verification.completed_count + 1,
        blocked_count=0,
        attested_count=verification.attested_count + 1,
    )

    # When / Then
    with pytest.raises(InvalidKrThemeDayOpenSmokeError):
        _ = attest_kr_theme_day_open_smoke(
            manifest,
            expanded,
            (*events, future),
            (*attestations, future_attestation),
            VERIFIED_AT,
        )

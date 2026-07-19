from __future__ import annotations

import datetime as dt
import stat
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import trading_agent.kr_theme_day_onboarding as onboarding
import trading_agent.private_immutable_file as private_file
from tests.test_kis_kr_market_projection import _opportunity
from tests.test_kr_theme_day_session_manifest import _identity
from tests.test_kr_theme_day_shadow_entry import _ledger
from tests.test_kr_theme_day_trial import OPPORTUNITY_VERSION, _calendar_evidence
from trading_agent.contract_outbox import append_opportunity_snapshot
from trading_agent.experiment_ledger_models import ExperimentTrialEvent, TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_theme_day_onboarding import (
    InvalidKrThemeDayOpportunityOnboardingError,
    KrThemeDayOpportunityOnboardingRequest,
    onboard_kr_theme_day_opportunity,
    onboarding_receipt_path,
)
from trading_agent.kr_theme_day_session_manifest import load_kr_theme_day_session_manifest
from trading_agent.signal_contract_models import EvidenceRef, OpportunitySnapshot

KST = ZoneInfo("Asia/Seoul")
ONBOARDED_AT = dt.datetime(2026, 7, 20, 9, 3, 45, tzinfo=KST)


def test_onboarding_binds_fresh_same_cycle_opportunity_to_preopen_trial(tmp_path: Path) -> None:
    # Given
    request = _prepared_request(tmp_path)

    # When
    first = onboard_kr_theme_day_opportunity(request)
    replay = onboard_kr_theme_day_opportunity(request)

    # Then
    manifest = load_kr_theme_day_session_manifest(request.manifest_path)
    assert first.created is True
    assert replay.created is False
    assert manifest.registered_at < dt.datetime(2026, 7, 20, 9, tzinfo=KST)
    assert manifest.opportunity_strategy_version == OPPORTUNITY_VERSION
    assert manifest.opportunity_id == request.opportunity_id
    assert first.receipt.onboarded_at == ONBOARDED_AT
    assert first.receipt.source_cycle_id == "kr-live-opportunity-onboarding-001"
    assert first.receipt.session_id == manifest.session_id
    assert stat.S_IMODE(request.manifest_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(onboarding_receipt_path(request.manifest_path).stat().st_mode) == 0o600


def test_onboarding_rejects_wrong_producer_stale_or_non_cycle_opportunity(tmp_path: Path) -> None:
    # Given
    base = _prepared_request(tmp_path)
    source = _same_cycle_opportunity()

    # When / Then
    for index, changed in enumerate(
        (
            source.model_copy(update={"producer_strategy_version": "kr-theme-manager-wrong"}),
            source.model_copy(update={"valid_until": ONBOARDED_AT}),
            source.model_copy(
                update={
                    "evidence_refs": tuple(
                        item for item in source.evidence_refs if item.namespace != "kr/collection_cycle"
                    )
                }
            ),
        )
    ):
        root = tmp_path / str(index)
        request = _prepared_request(root, opportunity=OpportunitySnapshot.model_validate(changed.model_dump()))
        with pytest.raises(InvalidKrThemeDayOpportunityOnboardingError):
            _ = onboard_kr_theme_day_opportunity(request)
        assert not request.manifest_path.exists()

    assert not base.manifest_path.exists()


def test_onboarding_recovers_receipt_first_manifest_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    request = _prepared_request(tmp_path)
    writer = onboarding.write_kr_theme_day_session_manifest

    def fail_manifest(_path: Path, _manifest: onboarding.KrThemeDaySessionManifest) -> None:
        raise OSError("fixture write interruption")

    monkeypatch.setattr(onboarding, "write_kr_theme_day_session_manifest", fail_manifest)

    # When
    with pytest.raises(InvalidKrThemeDayOpportunityOnboardingError):
        _ = onboard_kr_theme_day_opportunity(request)
    monkeypatch.setattr(onboarding, "write_kr_theme_day_session_manifest", writer)
    recovered = onboard_kr_theme_day_opportunity(request)

    # Then
    assert onboarding_receipt_path(request.manifest_path).is_file()
    assert recovered.created is True
    assert request.manifest_path.is_file()


def test_onboarding_rejects_noncanonical_existing_start_event(tmp_path: Path) -> None:
    # Given
    request = _prepared_request(tmp_path)
    ledger = ExperimentLedgerStore(request.paths.experiment_ledger)
    event = ExperimentTrialEvent(
        trial_id=request.trial_id,
        sequence=1,
        event_kind=TrialEventKind.STARTED,
        occurred_at=dt.datetime(2026, 7, 20, 9, 1, tzinfo=KST),
        artifact_sha256s=(),
        reason_codes=(),
        previous_event_key=None,
    )
    with ledger.writer() as writer:
        assert writer.append_multi_market_trial_event(event) is True

    # When / Then
    with pytest.raises(InvalidKrThemeDayOpportunityOnboardingError):
        _ = onboard_kr_theme_day_opportunity(request)
    assert not request.manifest_path.exists()


def test_receipt_interrupted_write_leaves_no_final_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    request = _prepared_request(tmp_path)
    original = private_file.os.fdopen

    def interrupt(_descriptor: int, _mode: str, *, encoding: str) -> None:
        del encoding
        raise OSError("fixture write interruption")

    monkeypatch.setattr(private_file.os, "fdopen", interrupt)

    # When
    with pytest.raises(InvalidKrThemeDayOpportunityOnboardingError):
        _ = onboard_kr_theme_day_opportunity(request)
    monkeypatch.setattr(private_file.os, "fdopen", original)
    recovered = onboard_kr_theme_day_opportunity(request)

    # Then
    assert recovered.created is True
    assert onboarding_receipt_path(request.manifest_path).is_file()


def _prepared_request(
    tmp_path: Path,
    *,
    opportunity: OpportunitySnapshot | None = None,
) -> KrThemeDayOpportunityOnboardingRequest:
    identity = _identity(tmp_path)
    ledger = _ledger(identity.paths.experiment_ledger, started=False)
    receipt, snapshot = _calendar_evidence()
    assert KisKrSessionCalendarStore(identity.paths.calendar_store).append(receipt, snapshot) is True
    selected = _same_cycle_opportunity() if opportunity is None else opportunity
    assert append_opportunity_snapshot(identity.paths.opportunity_outbox, selected) is True
    identity.paths.opportunity_outbox.chmod(0o600)
    trial_id = ledger.multi_market_trials()[0].registration.trial_id
    return KrThemeDayOpportunityOnboardingRequest(
        manifest_path=(tmp_path / "session.json").absolute(),
        paths=identity.paths,
        trial_id=trial_id,
        opportunity_id=selected.opportunity_id,
        onboarded_at=ONBOARDED_AT,
    )


def _same_cycle_opportunity() -> OpportunitySnapshot:
    observed_at = ONBOARDED_AT - dt.timedelta(seconds=15)
    source = _opportunity()
    evidence = tuple(
        sorted(
            (
                EvidenceRef(
                    namespace="kr/collection_cycle",
                    record_id="kr-live-opportunity-onboarding-001",
                    observed_at=observed_at,
                ),
                EvidenceRef(
                    namespace="kr/theme/state",
                    record_id="theme-1",
                    observed_at=observed_at,
                ),
            ),
            key=lambda item: item.canonical_id,
        )
    )
    return OpportunitySnapshot.model_validate(
        source.model_dump(mode="python")
        | {
            "producer_strategy_version": OPPORTUNITY_VERSION,
            "observed_at": observed_at,
            "valid_until": ONBOARDED_AT + dt.timedelta(minutes=5),
            "evidence_refs": evidence,
            "source_coverage": tuple(
                item.model_copy(update={"observed_at": observed_at}) for item in source.source_coverage
            ),
        }
    )

from __future__ import annotations

import datetime as dt
from pathlib import Path

from pydantic import ValidationError

from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.kis_kr_session_calendar_store import (
    InvalidKisKrSessionCalendarStoreError,
    KisKrSessionCalendarStore,
)
from trading_agent.kr_theme_day_composite_evidence import (
    KrThemeDayCompositeEvidenceRequest,
    require_exact_kr_theme_day_composite_evidence,
)
from trading_agent.kr_theme_day_intraday_io import (
    InvalidKrThemeDayOpportunitySourceError,
    kr_theme_day_opportunity_sha256,
    load_exact_kr_theme_opportunity,
)
from trading_agent.kr_theme_day_onboarding_models import (
    InvalidKrThemeDayOpportunityOnboardingError,
    KrThemeDayOpportunityOnboardingReceiptSource,
    KrThemeDayOpportunityOnboardingRequest,
    KrThemeDayOpportunityOnboardingResult,
    build_kr_theme_day_onboarding_receipt,
    load_kr_theme_day_onboarding_receipt,
    onboarding_receipt_path,
    write_kr_theme_day_onboarding_receipt,
)
from trading_agent.kr_theme_day_session_manifest import (
    InvalidKrThemeDaySessionManifestError,
    KrThemeDaySessionIdentity,
    KrThemeDaySessionManifest,
    build_kr_theme_day_session_manifest,
    write_kr_theme_day_session_manifest,
)
from trading_agent.kr_theme_day_trial import (
    InvalidKrThemeDayTrialError,
    require_exact_kr_theme_day_trial,
)
from trading_agent.kr_theme_day_trial_calendar import calendar_snapshot_id_from_evidence
from trading_agent.kr_theme_lane import KR_THEME_OPPORTUNITY_LANE
from trading_agent.multi_market_experiment_models import MultiMarketStrategyVersionRegistration
from trading_agent.multi_market_trial_store import StoredMultiMarketTrialRegistration
from trading_agent.signal_contract_models import OpportunitySnapshot


def onboard_kr_theme_day_opportunity(
    request: KrThemeDayOpportunityOnboardingRequest,
) -> KrThemeDayOpportunityOnboardingResult:
    try:
        request = KrThemeDayOpportunityOnboardingRequest.model_validate(request.model_dump(mode="python"))
        ledger = ExperimentLedgerReader(request.paths.experiment_ledger)
        stored_trial = _exact_trial(ledger, request)
        trial = stored_trial.registration
        composite = require_exact_kr_theme_day_composite_evidence(
            ledger,
            KrThemeDayCompositeEvidenceRequest(
                day_strategy_version=trial.strategy_version,
                evidence_budget=trial.evidence_budget,
                as_of=request.onboarded_at,
            ),
        )
        version = _exact_day_version(ledger, trial.strategy_version)
        opportunity = load_exact_kr_theme_opportunity(request.paths.opportunity_outbox, request.opportunity_id)
        source_cycle_id = _require_opportunity(request, opportunity, composite.opportunity_strategy_version)
        opportunity_sha256 = kr_theme_day_opportunity_sha256(opportunity)
        calendar_snapshot_id = calendar_snapshot_id_from_evidence(trial.evidence_budget)
        _require_calendar(request, calendar_snapshot_id, trial.planned_start)
        manifest = build_kr_theme_day_session_manifest(
            KrThemeDaySessionIdentity(
                strategy_version=trial.strategy_version,
                code_version=version.code_version,
                session_date=trial.planned_start,
                registered_at=trial.registered_at,
                onboarded_at=request.onboarded_at,
                calendar_snapshot_id=calendar_snapshot_id,
                opportunity_id=opportunity.opportunity_id,
                opportunity_strategy_version=opportunity.producer_strategy_version,
                opportunity_sha256=opportunity_sha256,
                symbol=opportunity.candidates[0].symbol,
                paths=request.paths,
            )
        )
        receipt = build_kr_theme_day_onboarding_receipt(
            KrThemeDayOpportunityOnboardingReceiptSource(
                trial_id=trial.trial_id,
                trial_registration_key=str(stored_trial.registration_key),
                composite_registration_key=composite.registration_key,
                session_id=manifest.session_id,
                day_strategy_version=trial.strategy_version,
                opportunity_strategy_version=opportunity.producer_strategy_version,
                opportunity_id=opportunity.opportunity_id,
                opportunity_sha256=opportunity_sha256,
                source_cycle_id=source_cycle_id,
                symbol=opportunity.candidates[0].symbol,
                session_date=trial.planned_start,
                registered_at=trial.registered_at,
                onboarded_at=request.onboarded_at,
                calendar_snapshot_id=calendar_snapshot_id,
            )
        )
        receipt_created = write_kr_theme_day_onboarding_receipt(
            onboarding_receipt_path(request.manifest_path),
            receipt,
        )
        manifest_created = _write_or_require_manifest(request, manifest)
        return KrThemeDayOpportunityOnboardingResult(receipt_created or manifest_created, manifest, receipt)
    except (
        AttributeError,
        InvalidExperimentLedgerSourceError,
        InvalidKisKrSessionCalendarStoreError,
        InvalidKrThemeDayOpportunityOnboardingError,
        InvalidKrThemeDayOpportunitySourceError,
        InvalidKrThemeDaySessionManifestError,
        InvalidKrThemeDayTrialError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrThemeDayOpportunityOnboardingError from None


def require_exact_kr_theme_day_onboarding(
    manifest_path: Path,
    manifest: KrThemeDaySessionManifest,
) -> None:
    receipt = load_kr_theme_day_onboarding_receipt(onboarding_receipt_path(manifest_path))
    if (
        receipt.session_id != manifest.session_id
        or receipt.onboarded_at != manifest.onboarded_at
        or receipt.opportunity_id != manifest.opportunity_id
        or receipt.opportunity_strategy_version != manifest.opportunity_strategy_version
        or receipt.opportunity_sha256 != manifest.opportunity_sha256
        or receipt.symbol != manifest.symbol
    ):
        raise InvalidKrThemeDayOpportunityOnboardingError
    replay = onboard_kr_theme_day_opportunity(
        KrThemeDayOpportunityOnboardingRequest(
            manifest_path=manifest_path.absolute(),
            paths=manifest.paths,
            trial_id=receipt.trial_id,
            opportunity_id=receipt.opportunity_id,
            onboarded_at=receipt.onboarded_at,
        )
    )
    if replay.created or replay.manifest != manifest or replay.receipt != receipt:
        raise InvalidKrThemeDayOpportunityOnboardingError


def _exact_trial(
    ledger: ExperimentLedgerReader,
    request: KrThemeDayOpportunityOnboardingRequest,
) -> StoredMultiMarketTrialRegistration:
    matches = tuple(item for item in ledger.multi_market_trials() if item.registration.trial_id == request.trial_id)
    if len(matches) != 1:
        raise InvalidKrThemeDayOpportunityOnboardingError
    stored = matches[0]
    require_exact_kr_theme_day_trial(ledger, stored.registration)
    events = ledger.multi_market_trial_events(request.trial_id)
    expected_start = dt.datetime.combine(
        stored.registration.planned_start,
        dt.time(9),
        tzinfo=dt.timezone(dt.timedelta(hours=9)),
    )
    if len(events) > 1 or any(
        item.event.event_kind is not TrialEventKind.STARTED
        or item.event.occurred_at != expected_start
        or item.event.occurred_at > request.onboarded_at
        for item in events
    ):
        raise InvalidKrThemeDayOpportunityOnboardingError
    return stored


def _exact_day_version(
    ledger: ExperimentLedgerReader,
    strategy_version: str,
) -> MultiMarketStrategyVersionRegistration:
    matches = tuple(
        item.registration
        for item in ledger.multi_market_strategy_versions()
        if item.registration.strategy_version == strategy_version
    )
    if len(matches) != 1:
        raise InvalidKrThemeDayOpportunityOnboardingError
    return matches[0]


def _require_opportunity(
    request: KrThemeDayOpportunityOnboardingRequest,
    opportunity: OpportunitySnapshot,
    expected_strategy_version: str,
) -> str:
    local_observed = opportunity.observed_at.astimezone(dt.timezone(dt.timedelta(hours=9)))
    local_onboarded = request.onboarded_at.astimezone(dt.timezone(dt.timedelta(hours=9)))
    cycle_ids = tuple(item.record_id for item in opportunity.evidence_refs if item.namespace == "kr/collection_cycle")
    if (
        opportunity.strategy_lane != KR_THEME_OPPORTUNITY_LANE
        or opportunity.producer_strategy_version != expected_strategy_version
        or opportunity.opportunity_id != request.opportunity_id
        or local_observed.date() != local_onboarded.date()
        or not opportunity.observed_at <= request.onboarded_at < opportunity.valid_until
        or len(cycle_ids) != 1
    ):
        raise InvalidKrThemeDayOpportunityOnboardingError
    return cycle_ids[0]


def _require_calendar(
    request: KrThemeDayOpportunityOnboardingRequest,
    snapshot_id: str,
    session_date: dt.date,
) -> None:
    matches = tuple(
        item
        for item in KisKrSessionCalendarStore(request.paths.calendar_store).snapshots()
        if item.snapshot_id == snapshot_id
    )
    days = (
        () if len(matches) != 1 else tuple(day for day in matches[0].payload.days if day.session_date == session_date)
    )
    if len(days) != 1 or not days[0].business_day or not days[0].trading_day or not days[0].open_day:
        raise InvalidKrThemeDayOpportunityOnboardingError


def _write_or_require_manifest(
    request: KrThemeDayOpportunityOnboardingRequest,
    manifest: KrThemeDaySessionManifest,
) -> bool:
    return write_kr_theme_day_session_manifest(request.manifest_path, manifest)

from __future__ import annotations

import datetime as dt
import functools
import json
import stat
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import run_kis_kr_ranking_collect
import run_kr_same_cycle_opportunity
import run_kr_volume_surge_derive
import run_ls_nws_collect
import run_opendart_collect
from tests.test_kis_kr_market_collect_cli import _eod_fixture, _fixture
from tests.test_kr_same_cycle_opportunity_cli import CODE_VERSION as OPPORTUNITY_CODE
from tests.test_kr_same_cycle_opportunity_cli import _write_policy
from tests.test_kr_theme_day_shadow_entry import CODE as DAY_CODE
from tests.test_kr_theme_day_shadow_entry import VERSION as DAY_VERSION
from tests.test_kr_theme_day_trial import DAY_MANIFEST, OPPORTUNITY_MANIFEST, _calendar_evidence
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_theme_day_composite import (
    KrThemeDayCompositeRegistrationRequest,
    register_kr_theme_day_composite,
)
from trading_agent.kr_theme_day_onboarding import (
    KrThemeDayOpportunityOnboardingRequest,
    onboard_kr_theme_day_opportunity,
    onboarding_receipt_path,
)
from trading_agent.kr_theme_day_session_audit_store import KrThemeDaySessionAuditStore
from trading_agent.kr_theme_day_session_manifest import KrThemeDaySessionPaths
from trading_agent.kr_theme_day_session_supervisor import (
    KrThemeDaySessionRuntime,
    run_kr_theme_day_session_tick,
)
from trading_agent.kr_theme_day_shadow_entry_store import KrThemeDayShadowEntryStore
from trading_agent.kr_theme_day_trial import (
    KrThemeDayTrialRegistrationRequest,
    register_kr_theme_day_shadow_trial,
)
from trading_agent.kr_theme_research_registration import (
    kr_theme_strategy_version,
    register_kr_theme_research_manifest,
)
from trading_agent.kr_theme_store import KrThemeStore
from trading_agent.kr_volume_surge import KrVolumeSurgeDerivationResult, derive_kr_volume_surge
from trading_agent.signal_contract_models import OpportunitySnapshot

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "kr_same_cycle_20260720"
KST = ZoneInfo("Asia/Seoul")
SESSION_DATE = dt.date(2026, 7, 20)
REGISTERED_AT = dt.datetime(2026, 7, 19, 8, 31, tzinfo=KST)
ONBOARDED_AT = dt.datetime(2026, 7, 20, 9, 3, 45, tzinfo=KST)
TICK_AT = dt.datetime(2026, 7, 20, 9, 4, 4, tzinfo=KST)
OPPORTUNITY_VERSION = kr_theme_strategy_version(OPPORTUNITY_CODE)


def test_same_cycle_opportunity_onboards_and_runs_first_shadow_tick(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    ledger_path = (tmp_path / "experiment.sqlite3").absolute()
    calendar_path = (tmp_path / "calendar.sqlite3").absolute()
    trial_id = _register_trial(tmp_path, ledger_path, calendar_path)
    policy = _write_policy(tmp_path)
    projection = (tmp_path / "projection").absolute()

    def fixture_clock() -> dt.datetime:
        return dt.datetime(2026, 7, 20, 9, 3, 20, tzinfo=KST)

    monkeypatch.setattr(
        run_opendart_collect,
        "collect_opendart_disclosures",
        functools.partial(
            run_opendart_collect.collect_opendart_disclosures,
            _clock=fixture_clock,
        ),
    )
    monkeypatch.setattr(
        run_ls_nws_collect,
        "collect_ls_nws_news",
        functools.partial(
            run_ls_nws_collect.collect_ls_nws_news,
            _clock=fixture_clock,
        ),
    )
    monkeypatch.setattr(
        run_kis_kr_ranking_collect,
        "collect_kis_kr_rankings",
        functools.partial(
            run_kis_kr_ranking_collect.collect_kis_kr_rankings,
            _clock=fixture_clock,
        ),
    )

    def derive_fixture(
        store: KrThemeStore,
        *,
        collection_cycle_id: str,
        collection_date: dt.date,
    ) -> KrVolumeSurgeDerivationResult:
        return derive_kr_volume_surge(
            store,
            collection_cycle_id=collection_cycle_id,
            collection_date=collection_date,
            _clock=fixture_clock,
        )

    monkeypatch.setattr(run_kr_volume_surge_derive, "derive_kr_volume_surge", derive_fixture)

    # When
    cycle_exit = run_kr_same_cycle_opportunity.main(
        _same_cycle_args(tmp_path, ledger_path, policy),
        clock=lambda: dt.datetime(2026, 7, 20, 9, 3, 30, tzinfo=KST),
    )
    outbox = projection / "opportunities.v1.jsonl"
    opportunity = OpportunitySnapshot.model_validate_json(outbox.read_text(encoding="utf-8").strip())
    paths = _session_paths(tmp_path, outbox)
    manifest_path = (tmp_path / "session.json").absolute()
    onboarded = onboard_kr_theme_day_opportunity(
        KrThemeDayOpportunityOnboardingRequest(
            manifest_path=manifest_path,
            paths=paths,
            trial_id=trial_id,
            opportunity_id=opportunity.opportunity_id,
            onboarded_at=ONBOARDED_AT,
        )
    )
    tick = run_kr_theme_day_session_tick(
        onboarded.manifest,
        TICK_AT,
        KrThemeDaySessionRuntime.production(clock=lambda: TICK_AT),
    )

    # Then
    assert cycle_exit == 0
    assert opportunity.producer_strategy_version == OPPORTUNITY_VERSION
    assert opportunity.observed_at < ONBOARDED_AT < opportunity.valid_until
    assert tick.blocked_phase is None
    assert len(tick.completed_phases) == 5
    assert len(KrThemeDayShadowEntryStore(paths.entry_store).entries()) == 1
    assert len(KrThemeDaySessionAuditStore(paths.audit_store).events(onboarded.manifest.session_id)) == 5
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(onboarding_receipt_path(manifest_path).stat().st_mode) == 0o600


def _register_trial(tmp_path: Path, ledger_path: Path, calendar_path: Path) -> str:
    ledger = ExperimentLedgerStore(ledger_path)
    payload = json.loads(OPPORTUNITY_MANIFEST.read_text(encoding="utf-8"))
    payload["strategy_version"] = OPPORTUNITY_VERSION
    payload["code_version"] = OPPORTUNITY_CODE
    payload["source_registered_at"] = "2026-07-19T08:00:00+09:00"
    payload["ledger_recorded_at"] = "2026-07-19T08:00:00+09:00"
    manifest_path = tmp_path / "opportunity-registration.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    _ = register_kr_theme_research_manifest(manifest_path, ledger)
    _ = register_kr_theme_research_manifest(DAY_MANIFEST, ledger)
    _ = register_kr_theme_day_composite(
        ledger,
        KrThemeDayCompositeRegistrationRequest(
            day_strategy_version=DAY_VERSION,
            opportunity_strategy_version=OPPORTUNITY_VERSION,
            registered_at=REGISTERED_AT - dt.timedelta(seconds=30),
        ),
        clock=lambda: REGISTERED_AT - dt.timedelta(seconds=30),
    )
    receipt, snapshot = _calendar_evidence()
    assert KisKrSessionCalendarStore(calendar_path).append(receipt, snapshot) is True
    return register_kr_theme_day_shadow_trial(
        ledger,
        KrThemeDayTrialRegistrationRequest(
            strategy_version=DAY_VERSION,
            code_version=DAY_CODE,
            session_date=SESSION_DATE,
            registered_at=REGISTERED_AT,
            calendar_snapshot=snapshot,
            opportunity_strategy_version=OPPORTUNITY_VERSION,
        ),
        clock=lambda: REGISTERED_AT,
    ).registration.trial_id


def _same_cycle_args(tmp_path: Path, ledger_path: Path, policy: Path) -> tuple[str, ...]:
    return (
        "--collection-cycle-id",
        "kr-live-opportunity-day-session-001",
        "--collection-date",
        SESSION_DATE.isoformat(),
        "--policy",
        str(policy),
        "--database",
        str(tmp_path / "kr-theme.sqlite3"),
        "--experiment-ledger",
        str(ledger_path),
        "--collection-output-dir",
        str(tmp_path / "collection"),
        "--run-root",
        str(tmp_path / "runs"),
        "--projection-output-dir",
        str(tmp_path / "projection"),
        "--output-dir",
        str(tmp_path / "cycle-report"),
        "--fixture-root",
        str(FIXTURES),
    )


def _session_paths(
    tmp_path: Path,
    outbox: Path,
) -> KrThemeDaySessionPaths:
    intraday_root = tmp_path / "intraday"
    eod_root = tmp_path / "eod"
    intraday_root.mkdir()
    eod_root.mkdir()
    return KrThemeDaySessionPaths(
        experiment_ledger=(tmp_path / "experiment.sqlite3").absolute(),
        calendar_store=(tmp_path / "calendar.sqlite3").absolute(),
        opportunity_outbox=outbox,
        receipt_store=(tmp_path / "market-receipts.sqlite3").absolute(),
        entry_store=(tmp_path / "entries.sqlite3").absolute(),
        exit_store=(tmp_path / "exits.sqlite3").absolute(),
        terminal_store=(tmp_path / "terminals.sqlite3").absolute(),
        review_store=(tmp_path / "reviews.sqlite3").absolute(),
        audit_store=(tmp_path / "session-audit.sqlite3").absolute(),
        output_root=(tmp_path / "session-output").absolute(),
        intraday_fixture_manifest=_fixture(intraday_root).absolute(),
        eod_fixture_manifest=_eod_fixture(eod_root).absolute(),
    )

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest

import run_day_session_service as cli
from tests.day_strategy_capsule_support import builtin_request
from tests.test_day_research_attempt_binding import _attempt, _binding, _family, _manifest, _version
from tests.test_kis_kr_market_projection import (
    _minute_body,
    _minute_row,
    _price_body,
    _quote_body,
)
from tests.test_kis_kr_market_projection import (
    _receipt as market_receipt,
)
from tests.test_kis_kr_session_calendar import _payload as calendar_payload
from tests.test_kis_kr_session_calendar import _row as calendar_row
from tests.test_kr_day_capsule_adapter import EVALUATED as KR_EVALUATED
from tests.test_kr_day_capsule_adapter import _request as kr_request
from tests.test_kr_day_decision_store import _event as kr_decision_event
from tests.test_us_day_situation_projection import EVALUATED_AT as US_EVALUATED
from tests.test_us_day_situation_projection import _inputs as us_inputs
from trading_agent.alpaca_models import AlpacaBar
from trading_agent.contract_outbox import append_opportunity_snapshot
from trading_agent.day_forward_trial_identity import ForwardExecutionLane
from trading_agent.day_forward_trial_models import DayForwardTrial
from trading_agent.day_session_service import (
    DaySessionServiceResult,
    _kr_active_capsule_ids,
    _materialize_kr_requests,
    run_day_session_service_tick,
)
from trading_agent.day_session_service_config import (
    KR_DAY_SESSION_LABEL,
    US_DAY_SESSION_LABEL,
    KrDaySessionServiceConfig,
    UsDaySessionServiceConfig,
    load_day_session_service_config,
    replace_day_session_launch_agent,
    verify_day_session_launch_agent,
)
from trading_agent.day_strategy_capsule import build_strategy_capsule, publish_day_strategy_capsule
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kis_kr_market_models import KisKrMarketReceiptKind
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_models import KisKrSessionCalendarReceipt
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluationRequest
from trading_agent.kr_day_capsule_shadow_models import (
    KrDayCapsuleShadowEvent,
    KrDayCapsuleShadowEventPayload,
    KrDayCapsuleShadowReason,
    KrDayCapsuleShadowStatus,
)
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.kr_day_decision_models import KrDayDecisionReasonCode, KrDayDecisionStatus
from trading_agent.kr_day_decision_store import KrDayDecisionStore
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_types import AttemptStatus
from trading_agent.us_equity_calendar import NEW_YORK
from trading_agent.us_strategy_day_input import UsStrategyDayInput, candidate_evidence
from trading_agent.us_strategy_research_source import UsLatestQuote

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def test_provision_writes_exact_bounded_launch_agents(tmp_path: Path) -> None:
    # Given: private config destinations for both market services.
    uv = Path(shutil.which("uv") or "/bin/false").resolve()
    _source_contracts(tmp_path / "sources")
    us_config = tmp_path / f"us-{SHA}.json"
    us_plist = tmp_path / "us.plist"
    kr_config = tmp_path / f"kr-{SHA}.json"
    kr_plist = tmp_path / "kr.plist"

    # When: each service is provisioned through the operator CLI.
    us = cli.main(_provision("us", uv, us_config, us_plist, tmp_path))
    kr = cli.main(_provision("kr", uv, kr_config, kr_plist, tmp_path))

    # Then: RunAtLoad and each market's bounded cadence are exact and secret-free.
    assert us == kr == 0
    for config_path, plist_path, label, interval_seconds in (
        (us_config, us_plist, US_DAY_SESSION_LABEL, 120),
        (kr_config, kr_plist, KR_DAY_SESSION_LABEL, 15),
    ):
        payload = plistlib.loads(plist_path.read_bytes())
        assert load_day_session_service_config(config_path).label == label
        assert payload["RunAtLoad"] is True
        assert payload["StartInterval"] == interval_seconds
        assert "KeepAlive" not in payload
        assert "EnvironmentVariables" not in payload
        assert verify_day_session_launch_agent(config_path, plist_path).ready


@pytest.mark.parametrize("market", ("us", "kr"))
def test_sunday_tick_is_service_success_without_child_or_state(
    market: Literal["us", "kr"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a configured service tick on Sunday in its local market.
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr("trading_agent.day_session_service._run_child", lambda command: calls.append(command))
    config = _config(market, tmp_path)

    # When: launchd invokes the tick.
    result = run_day_session_service_tick(
        config,
        clock=lambda: dt.datetime(2026, 8, 23, 3, 0, tzinfo=dt.UTC),
    )

    # Then: the service succeeds with explicit no-action before authority or input access.
    assert result == DaySessionServiceResult(market=market, status="no_action", reason="session_closed")
    assert calls == []
    assert (config.state_root / "health/day_session_service_health.json").is_file()


def test_missing_inputs_and_authority_mismatch_are_retryable_service_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an open-session US service with no current evidence, then a moved SHA.
    config = _config("us", tmp_path)
    monkeypatch.setattr("trading_agent.day_session_service._authority_reason", lambda _: None)

    # When: the source roots are empty.
    missing = run_day_session_service_tick(
        config,
        clock=lambda: dt.datetime(2026, 8, 24, 15, 0, tzinfo=dt.UTC),
    )
    monkeypatch.setattr("trading_agent.day_session_service._authority_reason", lambda _: "commit_mismatch")
    moved = run_day_session_service_tick(
        config,
        clock=lambda: dt.datetime(2026, 8, 24, 15, 0, tzinfo=dt.UTC),
    )

    # Then: both remain retryable launch-service successes with no silent work.
    assert missing.status == moved.status == "no_action"
    assert missing.reason == "source_missing"
    assert moved.reason == "commit_mismatch"


def test_open_kr_session_with_empty_projection_is_explicit_no_opportunity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an authorized open-session service and a completed empty projection.
    config = _config("kr", tmp_path)
    assert isinstance(config, KrDaySessionServiceConfig)
    cycle = config.source_root / "kr-research-20260824-090400" / "projection"
    cycle.mkdir(parents=True, mode=0o700)
    outbox = cycle / "opportunities.v1.jsonl"
    outbox.write_text("", encoding="utf-8")
    outbox.chmod(0o600)
    monkeypatch.setattr("trading_agent.day_session_service._authority_reason", lambda _: None)
    monkeypatch.setattr(
        "trading_agent.day_session_service._kr_active_capsule_ids",
        lambda *_: ("authorized-capsule",),
    )
    child_calls: list[tuple[str, ...]] = []

    def unexpected_child(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        child_calls.append(command)
        return subprocess.CompletedProcess(command, 0, '{"result":"processed"}', "")

    monkeypatch.setattr("trading_agent.day_session_service._run_child", unexpected_child)

    # When: the public day-session service consumes the empty projection.
    result = run_day_session_service_tick(
        config,
        clock=lambda: KR_EVALUATED.astimezone(dt.UTC),
    )

    # Then: it is a healthy no-op and no child decision process is started.
    assert result.status == "no_action"
    assert result.reason == "no_opportunity"
    assert result.decisions == ()
    assert child_calls == []


def test_open_session_real_collector_shaped_artifacts_reach_both_children(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: exact typed artifacts shaped like the installed US and KR collectors.
    us_config = _config("us", tmp_path / "us")
    kr_config = _config("kr", tmp_path / "kr")
    _write_us_sources(us_config.source_root)
    request_root = kr_config.source_root / "capsule_requests"
    request_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (request_root / "current.json").write_text(canonical_experiment_ledger_json(kr_request()) + "\n")
    os.chmod(request_root / "current.json", 0o600)
    monkeypatch.setattr("trading_agent.day_session_service._authority_reason", lambda _: None)

    # When: both open-session services execute their real child CLIs.
    us = run_day_session_service_tick(us_config, clock=lambda: US_EVALUATED)
    kr = run_day_session_service_tick(
        kr_config,
        clock=lambda: KR_EVALUATED.astimezone(dt.UTC),
    )

    # Then: US reaches the Day Agent stage and the empty installed KR ledger fails closed.
    assert us.reason != "source_missing"
    assert tuple((us_config.state_root / "us_day/session_sources").glob("us_day_source_*.json"))
    assert kr.status == "no_action"
    assert kr.reason == "capsule_authority_missing"
    assert not tuple((kr_config.state_root / "receipts").glob("kr_day_capsule_shadow_*.json"))


def test_failed_candidate_cutover_restores_current_launch_agent(tmp_path: Path) -> None:
    _source_contracts(tmp_path / "sources")
    uv = Path(shutil.which("uv") or "/bin/false").resolve()
    current_sha = "a" * 40
    candidate_sha = "b" * 40
    current_config = tmp_path / f"us-{current_sha}.json"
    candidate_config = tmp_path / f"us-{candidate_sha}.json"
    current_plist = tmp_path / "current.plist"
    candidate_plist = tmp_path / "candidate.plist"
    assert cli.main(_provision("us", uv, current_config, current_plist, tmp_path, current_sha)) == 0
    assert cli.main(_provision("us", uv, candidate_config, candidate_plist, tmp_path, candidate_sha)) == 0
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...]) -> int:
        calls.append(command)
        return 1 if "bootstrap" in command and str(candidate_plist) in command else 0

    assert not replace_day_session_launch_agent(
        current_config, current_plist, candidate_config, candidate_plist, runner=runner
    )
    assert calls[-2][1] == "bootstrap"
    assert str(current_plist) in calls[-2]


def test_kr_materializer_reads_cycle_calendar_market_and_ledger_stores(tmp_path: Path) -> None:
    evaluated_at = dt.datetime(2026, 8, 24, 9, 4, 4, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    config = _config("kr", tmp_path)
    assert isinstance(config, KrDaySessionServiceConfig)
    cycle_id = "kr-research-20260824-090400"
    source = kr_request()
    observed_at = evaluated_at - dt.timedelta(minutes=2)
    opportunity = source.opportunity.model_copy(
        update={
            "observed_at": observed_at,
            "valid_until": evaluated_at + dt.timedelta(seconds=50),
            "evidence_refs": (
                source.opportunity.evidence_refs[0].model_copy(
                    update={"record_id": cycle_id, "observed_at": observed_at}
                ),
            ),
            "source_coverage": tuple(
                item.model_copy(update={"observed_at": observed_at}) for item in source.opportunity.source_coverage
            ),
        }
    )
    cycle = config.source_root / cycle_id
    assert append_opportunity_snapshot(cycle / "projection/opportunities.v1.jsonl", opportunity)
    market_store = KisKrMarketReceiptStore(cycle / "005930.market.sqlite3")
    for kind, body, seconds in (
        (KisKrMarketReceiptKind.MINUTE_BARS, _minute_body_on(evaluated_at.date()), 2),
        (KisKrMarketReceiptKind.PRICE_STATUS, _price_body(), 2),
        (KisKrMarketReceiptKind.ORDER_BOOK, _quote_body(), 3),
    ):
        receipt = replace(
            market_receipt(kind, body, seconds=seconds),
            received_at=evaluated_at.replace(second=seconds),
        )
        assert market_store.append(receipt)
    calendar_receipt = KisKrSessionCalendarReceipt(
        base_date=evaluated_at.date() - dt.timedelta(days=5),
        received_at=evaluated_at - dt.timedelta(days=5, hours=1),
        status_code=200,
        content_type="application/json",
        raw_payload=calendar_payload(
            rows=(
                calendar_row("20260819", "Y", "Y", "Y", "Y"),
                calendar_row("20260824", "Y", "Y", "Y", "Y"),
                calendar_row("20260825", "Y", "Y", "Y", "Y"),
            )
        ),
    )
    calendar_snapshot = project_kis_kr_session_calendar(calendar_receipt)
    assert KisKrSessionCalendarStore(config.calendar_store).append(calendar_receipt, calendar_snapshot)
    ledger = ExperimentLedgerStore(config.experiment_ledger)
    family = _family()
    version = _version(family, market_id=MarketId.KR_EQUITIES)
    attempt = _attempt(0, AttemptStatus.SUCCEEDED)
    binding = _binding(attempt, version)
    with ledger.writer() as writer:
        assert writer.register_strategy_research(_manifest())
        assert writer.register_day_hypothesis_family(family)
        assert writer.register_day_hypothesis_version(version)
        assert writer.append_strategy_research_attempt(attempt)
        assert writer.register_day_research_attempt_binding(binding)
    capsule_request = replace(
        builtin_request(market_id=MarketId.KR_EQUITIES),
        hypothesis_version_id=version.hypothesis_version_id,
        attempt_binding_id=binding.binding_id,
        artifact_ref=binding.artifact_ref,
        evaluation_cadence=version.evaluation_cadence,
        entry_rule=version.entry_rule,
        exit_rule=version.exit_rule,
        stop_rule=version.stop_rule,
        cost_model=version.cost_model,
        protocol_sha256=version.protocol_sha256,
        published_at=binding.bound_at + dt.timedelta(minutes=1),
    )
    capsule, _ = publish_day_strategy_capsule(ledger, capsule_request)
    eligible_at = evaluated_at.astimezone(dt.UTC) - dt.timedelta(minutes=1)
    trial_payload = {
        "schema_version": 1,
        "trial_id": "",
        "capsule_id": capsule.capsule_id,
        "hypothesis_version_id": capsule.hypothesis_version_id,
        "market_id": MarketId.KR_EQUITIES,
        "execution_lane": ForwardExecutionLane.FORWARD_PROBE,
        "session_id": f"XKRX-{evaluated_at.date().isoformat()}",
        "session_date": evaluated_at.date(),
        "calendar_snapshot_id": f"calendar://official/XKRX/{calendar_snapshot.snapshot_id}",
        "cost_model_sha256": hashlib.sha256(
            canonical_experiment_ledger_json(capsule.cost_model).encode()
        ).hexdigest(),
        "source_refs_sha256": hashlib.sha256(
            json.dumps(version.source_refs, ensure_ascii=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "evidence_schema_sha256": hashlib.sha256(
            json.dumps(capsule.evidence_schema, ensure_ascii=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "preregistered_at": eligible_at - dt.timedelta(seconds=30),
        "registration_completed_bar_at": eligible_at - dt.timedelta(minutes=1),
        "first_eligible_completed_bar_at": eligible_at,
        "trading_authority": False,
        "profitability_claim": False,
    }
    trial = DayForwardTrial.model_validate(
        trial_payload | {"trial_id": DayForwardTrial.canonical_id_for(trial_payload)}
    )
    with ledger.writer() as writer:
        assert writer.register_day_forward_trial(trial)

    assert _kr_active_capsule_ids(config.experiment_ledger, eligible_at - dt.timedelta(microseconds=1)) == ()
    assert _kr_active_capsule_ids(config.experiment_ledger, evaluated_at.astimezone(dt.UTC)) == (
        capsule.capsule_id,
    )

    paths = _materialize_kr_requests(config, evaluated_at.astimezone(dt.UTC), (capsule.capsule_id,))
    replay_paths = _materialize_kr_requests(config, evaluated_at.astimezone(dt.UTC), (capsule.capsule_id,))
    stale_paths = _materialize_kr_requests(
        config,
        (evaluated_at + dt.timedelta(seconds=40)).astimezone(dt.UTC),
        (capsule.capsule_id,),
    )
    expired_paths = _materialize_kr_requests(
        config,
        (evaluated_at + dt.timedelta(minutes=3)).astimezone(dt.UTC),
        (capsule.capsule_id,),
    )
    materialized = kr_request().model_validate_json(paths[0].read_text())
    _append_active_shadow(config, materialized)
    next_evaluated_at = evaluated_at + dt.timedelta(minutes=1)
    minute_payload = json.loads(_minute_body_on(evaluated_at.date()))
    next_row = _minute_row("090500", "103", "105", "102", "104", "100", "59970")
    next_row["stck_bsop_date"] = evaluated_at.strftime("%Y%m%d")
    minute_payload["output2"].insert(0, next_row)
    next_bodies = (
        (KisKrMarketReceiptKind.MINUTE_BARS, json.dumps(minute_payload).encode(), 2),
        (KisKrMarketReceiptKind.PRICE_STATUS, _price_body(), 2),
        (KisKrMarketReceiptKind.ORDER_BOOK, _quote_body(accepted_hour="090503"), 3),
    )
    for kind, body, seconds in next_bodies:
        receipt = replace(
            market_receipt(kind, body, seconds=seconds),
            received_at=next_evaluated_at.replace(second=seconds),
        )
        assert market_store.append(receipt)
    newer_cycle_id = "kr-research-20260824-090500"
    newer_observed_at = next_evaluated_at - dt.timedelta(seconds=20)
    newer_opportunity = opportunity.model_copy(
        update={
            "opportunity_id": "KR-THEME-OPPORTUNITY-NEWER",
            "observed_at": newer_observed_at,
            "valid_until": next_evaluated_at + dt.timedelta(seconds=1),
            "evidence_refs": (
                opportunity.evidence_refs[0].model_copy(
                    update={"record_id": newer_cycle_id, "observed_at": newer_observed_at}
                ),
            ),
            "source_coverage": tuple(
                item.model_copy(update={"observed_at": newer_observed_at})
                for item in opportunity.source_coverage
            ),
        }
    )
    newer_cycle = config.source_root / newer_cycle_id
    assert append_opportunity_snapshot(
        newer_cycle / "projection/opportunities.v1.jsonl",
        newer_opportunity,
    )
    newer_market_store = KisKrMarketReceiptStore(newer_cycle / "005930.market.sqlite3")
    for kind, body, seconds in next_bodies:
        receipt = replace(
            market_receipt(kind, body, seconds=seconds),
            received_at=next_evaluated_at.replace(second=seconds),
        )
        assert newer_market_store.append(receipt)
    next_paths = _materialize_kr_requests(
        config,
        next_evaluated_at.astimezone(dt.UTC),
        (capsule.capsule_id,),
    )

    managed = kr_request().model_validate_json(next_paths[0].read_text())
    assert replay_paths == paths
    assert stale_paths == ()
    assert expired_paths == ()
    assert next_paths != paths
    assert paths[0].is_file() and next_paths[0].is_file()
    assert materialized.capsule.capsule_id == capsule.capsule_id
    assert materialized.opportunity.opportunity_id == opportunity.opportunity_id
    assert materialized.market.symbol == "005930"
    assert managed.opportunity.opportunity_id == opportunity.opportunity_id
    assert managed.opportunity.evidence_refs[0].record_id == cycle_id
    assert managed.calendar.snapshot_id == materialized.calendar.snapshot_id
    assert managed.opportunity.candidates[0].symbol == materialized.opportunity.candidates[0].symbol

    sibling, _ = publish_day_strategy_capsule(
        ledger,
        replace(capsule_request, risk_policy_ref="risk-policy://day-research/sibling-v1"),
    )
    mixed_evaluated_at = next_evaluated_at + dt.timedelta(seconds=4)

    mixed_paths = _materialize_kr_requests(
        config,
        mixed_evaluated_at.astimezone(dt.UTC),
        (capsule.capsule_id, sibling.capsule_id),
    )

    assert len(mixed_paths) == 1
    mixed = kr_request().model_validate_json(mixed_paths[0].read_text())
    assert mixed.capsule.capsule_id == capsule.capsule_id
    assert mixed.opportunity.opportunity_id == opportunity.opportunity_id
    child = subprocess.run(
        (
            sys.executable,
            str(ROOT / "run_kr_day_capsule_shadow.py"),
            "--request",
            str(mixed_paths[0]),
                "--store",
                str(config.state_root / "kr-day-capsule-shadow.sqlite3"),
                "--decision-store",
                str(config.state_root / "kr-day-decisions.sqlite3"),
            ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    child_payload = json.loads(child.stdout)
    assert child.returncode == 0
    assert child_payload["events"][0]["status"] == "active"
    assert len(KrDayCapsuleShadowStore(config.state_root / "kr-day-capsule-shadow.sqlite3").events()) == 2


def test_public_kr_tick_audits_distinct_opportunities_for_active_and_current_siblings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one historical ACTIVE lineage and one distinct current sibling on the same tick.
    config = _config("kr", tmp_path)
    assert isinstance(config, KrDaySessionServiceConfig)
    config.state_root.mkdir(mode=0o700)
    active_request = kr_request()
    sibling_capsule = build_strategy_capsule(
        replace(
            builtin_request(market_id=MarketId.KR_EQUITIES),
            risk_policy_ref="risk-policy://day-research/current-sibling-v1",
        )
    )
    sibling_request = active_request.model_copy(
        update={
            "capsule": sibling_capsule,
            "opportunity": active_request.opportunity.model_copy(
                update={"opportunity_id": "KR-THEME-OPPORTUNITY-CURRENT-SIBLING"}
            ),
        }
    )
    _append_active_shadow(config, active_request)
    request_root = tmp_path / "requests"
    request_root.mkdir(mode=0o700)
    paths = tuple(request_root / name for name in ("active.json", "sibling.json"))
    for path, request in zip(paths, (active_request, sibling_request), strict=True):
        assert publish_private_immutable_text(
            path,
            canonical_experiment_ledger_json(request) + "\n",
        )
    monkeypatch.setattr("trading_agent.day_session_service._authority_reason", lambda _: None)
    monkeypatch.setattr(
        "trading_agent.day_session_service._kr_active_capsule_ids",
        lambda *_: (active_request.capsule.capsule_id, sibling_capsule.capsule_id),
    )
    monkeypatch.setattr(
        "trading_agent.day_session_service._materialize_kr_requests",
        lambda *_: paths,
    )
    child_commands: list[tuple[str, ...]] = []

    def run_child(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        child_commands.append(command)
        return subprocess.CompletedProcess(command, 0, '{"result":"processed"}', "")

    monkeypatch.setattr(
        "trading_agent.day_session_service._run_child",
        run_child,
    )

    # When: the public KR day-session tick processes both immutable request artifacts.
    result = run_day_session_service_tick(
        config,
        clock=lambda: active_request.evaluated_at.astimezone(dt.UTC),
    )

    # Then: both capsule-specific opportunity decisions remain auditable instead of being dropped.
    stored = KrDayDecisionStore(config.state_root / "kr-day-decisions.sqlite3").events()
    assert len(result.decisions) == 2
    assert len(stored) == 2
    assert {event.opportunity_id for event in stored} == {
        active_request.opportunity.opportunity_id,
        sibling_request.opportunity.opportunity_id,
    }
    assert child_commands
    decision_option = child_commands[0].index("--decision-store")
    assert child_commands[0][decision_option + 1] == str(
        config.state_root / "kr-day-decisions.sqlite3"
    )


def test_public_kr_tick_expires_visible_plan_without_new_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one user-visible ARMED plan whose deadline has elapsed without a new source cycle.
    config = _config("kr", tmp_path)
    assert isinstance(config, KrDaySessionServiceConfig)
    config.state_root.mkdir(mode=0o700)
    armed = kr_decision_event(
        status=KrDayDecisionStatus.ARMED,
        reason_codes=(KrDayDecisionReasonCode.CONDITIONAL_TRIGGER_PENDING,),
    )
    decisions = KrDayDecisionStore(config.state_root / "kr-day-decisions.sqlite3")
    assert decisions.append(armed)
    monkeypatch.setattr("trading_agent.day_session_service._authority_reason", lambda _: None)
    monkeypatch.setattr("trading_agent.day_session_service._kr_active_capsule_ids", lambda *_: ())
    observed_at = dt.datetime(2026, 8, 24, 15, 30, 30, tzinfo=dt.timezone(dt.timedelta(hours=9)))

    # When: the public 120-second service path runs and then replays after the deadline.
    first = run_day_session_service_tick(config, clock=lambda: observed_at)
    replay = run_day_session_service_tick(config, clock=lambda: observed_at + dt.timedelta(seconds=1))

    # Then: EXPIRED closes the decision and Hermes thread exactly once without source materialization.
    history = decisions.events()
    deliveries = HermesDeliveryStore(config.hermes_delivery_database).events()
    assert tuple(event.status for event in history) == (
        KrDayDecisionStatus.ARMED,
        KrDayDecisionStatus.EXPIRED,
    )
    assert first.decisions == (history[-1],)
    assert replay.decisions == ()
    assert tuple(event.kind for event in deliveries) == (
        HermesDeliveryKind.ACTIONABLE,
        HermesDeliveryKind.INVALIDATION,
    )
    assert deliveries[1].root_delivery_id == deliveries[0].delivery_id
    assert "PRICE_SETUP_EXPIRED" in deliveries[1].rendered_text


def _append_active_shadow(
    config: KrDaySessionServiceConfig,
    request: KrDayCapsuleEvaluationRequest,
) -> None:
    cursor = request.bars[-1].end_at
    payload = KrDayCapsuleShadowEventPayload(
        capsule_id=request.capsule.capsule_id,
        evaluation_id="d" * 64,
        session_date=request.evaluated_at.astimezone(dt.timezone(dt.timedelta(hours=9))).date(),
        calendar_snapshot_id=request.calendar.snapshot_id,
        collection_cycle_id=request.opportunity.evidence_refs[0].record_id,
        symbol=request.opportunity.candidates[0].symbol,
        attempted_bar_cursor=cursor,
        accepted_bar_cursor=cursor,
        previous_event_id=None,
        status=KrDayCapsuleShadowStatus.ACTIVE,
        reason=KrDayCapsuleShadowReason.ENTRY,
        signal_id="active-signal",
        entry_price=Decimal("104"),
        stop_price=Decimal("90"),
        target_prices=(Decimal("200"),),
        occurred_at=request.evaluated_at,
        evaluation_payload_sha256="e" * 64,
        bar_payload_sha256="f" * 64,
    )
    event = KrDayCapsuleShadowEvent.model_validate(
        payload.model_dump(mode="python")
        | {"event_id": KrDayCapsuleShadowEvent.canonical_id_for(payload)}
    )
    assert KrDayCapsuleShadowStore(
        config.state_root / "kr-day-capsule-shadow.sqlite3"
    ).append(event)


def _minute_body_on(session_date: dt.date) -> bytes:
    payload = json.loads(_minute_body())
    for row in payload["output2"]:
        row["stck_bsop_date"] = session_date.strftime("%Y%m%d")
    return json.dumps(payload).encode()


def _config(
    market: Literal["us", "kr"],
    root: Path,
) -> UsDaySessionServiceConfig | KrDaySessionServiceConfig:
    _source_contracts(root / "sources")
    common = {
        "project_root": ROOT,
        "expected_commit": SHA,
        "uv_path": Path(shutil.which("uv") or "/bin/false").resolve(),
        "source_root": root / "sources",
        "state_root": root / "state",
    }
    if market == "us":
        return UsDaySessionServiceConfig(**common)
    return KrDaySessionServiceConfig(
        **common,
        calendar_store=root / "calendar/calendar.sqlite3",
        experiment_ledger=root / "ledger/experiment.sqlite3",
        hermes_delivery_database=root / "hermes/delivery.sqlite3",
    )


def _provision(
    market: str,
    uv: Path,
    config: Path,
    plist: Path,
    root: Path,
    sha: str = SHA,
) -> tuple[str, ...]:
    arguments = (
        "provision",
        "--market",
        market,
        "--project-root",
        str(ROOT),
        "--expected-commit",
        sha,
        "--uv-path",
        str(uv),
        "--source-root",
        str(root / "sources"),
        "--state-root",
        str(root / "state"),
        "--config",
        str(config),
        "--plist",
        str(plist),
    )
    if market == "kr":
        return (
            *arguments,
            "--calendar-store",
            str(root / "calendar/calendar.sqlite3"),
            "--experiment-ledger",
            str(root / "ledger/experiment.sqlite3"),
            "--hermes-delivery-database",
            str(root / "hermes/delivery.sqlite3"),
        )
    return arguments


def _source_contracts(root: Path) -> None:
    for path in (
        root,
        root / "capsule_requests",
        root.parent / "calendar",
        root.parent / "ledger",
        root.parent / "hermes",
    ):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)


def _write_us_sources(root: Path) -> None:
    inputs = us_inputs()
    bars_by_symbol = {
        tick.bars[-1].symbol: tuple(
            AlpacaBar(
                t=bar.timestamp,
                o=bar.open,
                h=bar.high,
                l=bar.low,
                c=bar.close,
                v=bar.volume,
                n=1,
                vw=bar.close,
            )
            for bar in tick.bars
        )
        for tick in inputs.completed_bars
    }
    quotes_by_symbol = {
        quote.symbol: UsLatestQuote(
            symbol=quote.symbol,
            bid=float(quote.bid),
            ask=float(quote.ask),
            observed_at=quote.provider_observed_at,
        )
        for quote in inputs.quotes
    }
    day_input = UsStrategyDayInput(
        opportunity=inputs.scanner.opportunity,
        market_context=inputs.market_context,
        articles=inputs.articles,
        news_evidence=inputs.news_evidence,
        candidates=candidate_evidence(
            inputs.scanner.opportunity,
            bars_by_symbol,
            quotes_by_symbol,
            US_EVALUATED,
        ),
        materialized_at=US_EVALUATED,
    )
    session = root / US_EVALUATED.astimezone(NEW_YORK).strftime("%Y%m%d")
    session.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = session / f"{day_input.input_id}.day-input.json"
    _ = publish_private_immutable_text(path, day_input.model_dump_json() + "\n")

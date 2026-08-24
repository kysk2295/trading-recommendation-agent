from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import os
import plistlib
import subprocess
from pathlib import Path

import pytest

import run_kr_day_close_service as cli
from tests.kr_day_close_service_support import ROOT, close_fixture
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_day_close_service import KrDayCloseRuntime, run_kr_day_close_service
from trading_agent.kr_day_close_service_config import (
    KrDayCloseServiceConfig,
    write_kr_day_close_service_config,
)
from trading_agent.kr_day_close_service_launchd import (
    provision_kr_day_close_launch_agent,
    verify_kr_day_close_launch_agent,
)
from trading_agent.kr_day_close_service_state import KrDayCloseServiceHealth
from trading_agent.private_immutable_file import read_private_text

SCRIPT = ROOT / "run_kr_day_close_service.py"


def test_public_help_exposes_only_private_config_path() -> None:
    # Given: the installed KR day-close service CLI.
    command = ("uv", "run", "python", str(SCRIPT), "--help")

    # When: its public surface is rendered through the real process boundary.
    completed = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)

    # Then: callers can bind only one private config, never provider or trading controls.
    assert completed.returncode == 0
    assert "--config" in completed.stdout
    assert not any(
        token in completed.stdout.lower()
        for token in ("credential", "endpoint", "account", "order", "position", "provider", "broker")
    )


def test_preclose_and_holiday_are_truthful_noops_with_health(tmp_path: Path) -> None:
    # Given: one official open session before close and one official exchange holiday.
    preclose = close_fixture(tmp_path / "preclose")
    holiday = close_fixture(tmp_path / "holiday", open_day=False)

    # When: launchd invokes both service configurations.
    early = run_kr_day_close_service(preclose.config, _runtime(preclose.pre_close))
    closed = run_kr_day_close_service(holiday.config, _runtime(holiday.post_close))

    # Then: neither completes research, while each persists exact no-op health.
    assert (early.status, early.reason, early.complete) == ("no_action", "pre_close", False)
    assert (closed.status, closed.reason, closed.complete) == ("no_action", "session_not_open", False)
    assert _health(preclose.config.health_root).reason == "pre_close"
    assert _health(holiday.config.health_root).reason == "session_not_open"
    assert not preclose.config.completion_root.exists()
    assert not holiday.config.completion_root.exists()


def test_postclose_replay_completes_once_and_delivers_one_summary(tmp_path: Path) -> None:
    # Given: authoritative calendar, capsule/trial, decision, and terminal Shadow history.
    fixture = close_fixture(tmp_path)

    # When: the same official close is executed and replayed.
    first = run_kr_day_close_service(fixture.config, _runtime(fixture.post_close))
    replay = run_kr_day_close_service(fixture.config, _runtime(fixture.post_close))

    # Then: all durable stages exist once and the report-ID summary is deduplicated.
    assert (first.status, first.complete, first.summary_inserted) == ("completed", True, 1)
    assert (replay.status, replay.complete, replay.summary_inserted) == ("completed", True, 0)
    assert replay.report_id == first.report_id
    assert len(tuple(fixture.config.completion_root.glob("*.json"))) == 1
    events = HermesDeliveryStore(fixture.config.hermes_delivery_database).events()
    assert tuple(event.kind for event in events) == (HermesDeliveryKind.DAILY_SUMMARY,)
    assert "challenger 결정 active/queued" in events[0].rendered_text
    assert "신규 challenger 등록: 0" in events[0].rendered_text
    assert first.mutation_count == 0


def test_postclose_summary_reports_registered_challenger_result(tmp_path: Path) -> None:
    # Given: durable generated KR parent, Champion, typed patch, runtime, and XKRX loop inputs.
    fixture = close_fixture(tmp_path, configured_loop=True)

    # When: the default configured close path runs and replays the real loop.
    result = run_kr_day_close_service(fixture.config, _runtime(fixture.post_close))
    replay = run_kr_day_close_service(fixture.config, _runtime(fixture.post_close))

    # Then: one future-only Challenger exists, Champion is unchanged, and durable count remains one.
    event = HermesDeliveryStore(fixture.config.hermes_delivery_database).events()[0]
    versions = DayAgentVersionStore(fixture.config.state_root / "day-agent-versions.sqlite3")
    champion = versions.reader().champion()
    challengers = versions.reader().challengers()
    assert champion is not None
    assert (result.complete, result.challenger_count, replay.challenger_count) == (True, 1, 1)
    assert len(challengers) == 1
    assert challengers[0].parent_version_id == champion.version_id
    assert challengers[0].created_session_date == dt.date(2026, 8, 26)
    assert len(tuple(fixture.config.completion_root.glob("*.json"))) == 1
    assert "신규 challenger 등록: 1" in event.rendered_text


def test_configured_calendar_mismatch_blocks_before_summary_and_completion(tmp_path: Path) -> None:
    # Given: eligible configured authorities whose future loop calendar differs from the close report.
    fixture = close_fixture(
        tmp_path,
        configured_loop=True,
        loop_calendar_snapshot_id="b" * 64,
    )

    # When: the default close path validates the configured slow-loop authority.
    result = run_kr_day_close_service(fixture.config, _runtime(fixture.post_close))

    # Then: loop failure remains incomplete and publishes neither summary nor completion.
    assert (result.status, result.stage, result.complete) == ("blocked", "loop", False)
    assert HermesDeliveryStore(fixture.config.hermes_delivery_database).events() == ()
    assert not fixture.config.completion_root.exists()


def test_us_loop_bundle_blocks_before_challenger_summary_and_completion(tmp_path: Path) -> None:
    # Given: a configured KR close whose loop authority contains a US template and AAPL replay source.
    fixture = close_fixture(tmp_path, configured_loop=True, us_loop_inputs=True)

    # When: the default close path validates market-bound loop authority.
    result = run_kr_day_close_service(fixture.config, _runtime(fixture.post_close))

    # Then: publication is blocked with no challenger, summary, or completion receipt.
    versions = DayAgentVersionStore(fixture.config.state_root / "day-agent-versions.sqlite3")
    assert (result.status, result.stage, result.complete, result.challenger_count) == (
        "blocked",
        "loop",
        False,
        0,
    )
    assert versions.reader().challengers() == ()
    assert HermesDeliveryStore(fixture.config.hermes_delivery_database).events() == ()
    assert not fixture.config.completion_root.exists()


@pytest.mark.parametrize("crash_stage", ("report", "policy", "loop", "summary", "completion"))
def test_partial_crash_recovers_without_duplicate_summary(
    crash_stage: str,
    tmp_path: Path,
) -> None:
    # Given: a one-shot crash immediately after one durable publication stage.
    fixture = close_fixture(tmp_path)

    def crash(stage: str) -> None:
        if stage == crash_stage:
            raise OSError

    # When: the failed invocation is restarted with the same evidence.
    failed = run_kr_day_close_service(
        fixture.config,
        KrDayCloseRuntime(clock=lambda: fixture.post_close, stage_observer=crash),
    )
    recovered = run_kr_day_close_service(fixture.config, _runtime(fixture.post_close))

    # Then: failure is incomplete and restart converges to one receipt and one summary.
    assert (failed.status, failed.complete) == ("blocked", False)
    assert (recovered.status, recovered.complete) == ("completed", True)
    assert len(tuple(fixture.config.completion_root.glob("*.json"))) == 1
    assert len(HermesDeliveryStore(fixture.config.hermes_delivery_database).events()) == 1


@pytest.mark.parametrize("source", ("calendar", "decision", "shadow", "experiment"))
def test_corrupt_authority_fails_visible_without_completion(source: str, tmp_path: Path) -> None:
    # Given: a valid close fixture with one required local authority made corrupt.
    fixture = close_fixture(tmp_path)
    path = {
        "calendar": fixture.config.calendar_store,
        "decision": fixture.config.decision_store,
        "shadow": fixture.config.shadow_store,
        "experiment": fixture.config.experiment_ledger,
    }[source]
    path.write_bytes(b"corrupt")
    os.chmod(path, 0o600)

    # When: finalization queries its local stores.
    result = run_kr_day_close_service(fixture.config, _runtime(fixture.post_close))

    # Then: the exact stage remains visibly incomplete and no completion is fabricated.
    health = _health(fixture.config.health_root)
    assert (result.status, result.complete) == ("blocked", False)
    assert (health.complete, health.stage) == (False, result.stage)
    assert not fixture.config.completion_root.exists()


def test_missing_decision_ledger_fails_visible_without_completion(tmp_path: Path) -> None:
    # Given: a post-close session whose selected decision store is missing.
    fixture = close_fixture(tmp_path)
    fixture.config.decision_store.unlink()

    # When: finalization queries the coherent local state root.
    result = run_kr_day_close_service(fixture.config, _runtime(fixture.post_close))

    # Then: health is blocked and the immutable completion receipt is absent.
    assert (result.status, result.complete) == ("blocked", False)
    assert _health(fixture.config.health_root).reason.endswith("source_invalid")
    assert not fixture.config.completion_root.exists()


def test_foreign_snapshot_shadow_history_cannot_finalize_selected_trial(tmp_path: Path) -> None:
    # Given: the trial binds the official snapshot but every same-day Shadow event binds another.
    fixture = close_fixture(tmp_path, shadow_snapshot_id="f" * 64)

    # When: post-close finalization reads the otherwise coherent local ledgers.
    result = run_kr_day_close_service(fixture.config, _runtime(fixture.post_close))

    # Then: it blocks before publishing a report or immutable completion.
    assert (result.status, result.complete) == ("blocked", False)
    assert not fixture.config.report_root.exists()
    assert not fixture.config.completion_root.exists()


def test_historical_calendar_snapshot_cannot_authorize_current_close(tmp_path: Path) -> None:
    # Given: a prior-date official snapshot happens to include the selected session in its horizon.
    fixture = close_fixture(
        tmp_path,
        calendar_base_date=dt.date(2026, 8, 23),
    )

    # When: the close runtime selects calendar authority for the current local session.
    result = run_kr_day_close_service(fixture.config, _runtime(fixture.post_close))

    # Then: historical calendar authority is stale and cannot create report or completion state.
    assert (result.status, result.complete) == ("blocked", False)
    assert not fixture.config.report_root.exists()
    assert not fixture.config.completion_root.exists()


def test_active_without_exact_close_evidence_remains_incomplete(tmp_path: Path) -> None:
    # Given: persisted capsule/trial authority whose latest Shadow event remains ACTIVE.
    fixture = close_fixture(tmp_path, terminal=False)

    # When: close finalization cannot find an exact persisted close-price event.
    result = run_kr_day_close_service(fixture.config, _runtime(fixture.post_close))

    # Then: it never synthesizes a close outcome or marks the session complete.
    assert (result.status, result.complete, result.report_id) == ("blocked", False, None)
    assert not fixture.config.report_root.exists()
    assert not fixture.config.completion_root.exists()


def test_concurrent_invocations_serialize_to_one_completion(tmp_path: Path) -> None:
    # Given: two launchd recovery invocations for the same official close.
    fixture = close_fixture(tmp_path)

    # When: both enter through separate worker threads concurrently.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            pool.map(
                lambda _index: run_kr_day_close_service(fixture.config, _runtime(fixture.post_close)),
                range(2),
            )
        )

    # Then: the process lease serializes them and only one durable summary is inserted.
    assert all(result.complete for result in results)
    assert sorted(result.summary_inserted for result in results) == [0, 1]
    assert len(tuple(fixture.config.completion_root.glob("*.json"))) == 1
    assert len(HermesDeliveryStore(fixture.config.hermes_delivery_database).events()) == 1


def test_config_and_launch_agent_pin_exact_recovery_schedule(tmp_path: Path) -> None:
    # Given: a commit-versioned private service configuration.
    fixture = close_fixture(tmp_path)
    assert write_kr_day_close_service_config(fixture.config_path, fixture.config)

    # When: its LaunchAgent is provisioned and independently verified.
    plist_path = provision_kr_day_close_launch_agent(fixture.config, fixture.config_path)
    verification = verify_kr_day_close_launch_agent(fixture.config, fixture.config_path)
    payload = plistlib.loads(plist_path.read_bytes())

    # Then: three post-close times bind exact inputs without weekdays or secret fields.
    assert verification.ready and verification.invocation_count == 3
    assert all("Weekday" not in interval for interval in payload["StartCalendarInterval"])
    assert payload["ProgramArguments"][-1] == str(fixture.config_path)
    assert fixture.config.expected_commit in plist_path.name
    assert "EnvironmentVariables" not in payload


def test_close_config_accepts_external_read_only_experiment_ledger(tmp_path: Path) -> None:
    # Given: production keeps the shared research ledger outside the KR session-owned state root.
    fixture = close_fixture(tmp_path / "fixture")
    shared_ledger = tmp_path / "experiment-control" / "experiment-ledger.sqlite3"

    # When: the close service binds that shared read-only dependency.
    config = KrDayCloseServiceConfig.model_validate(
        fixture.config.model_dump(mode="python") | {"experiment_ledger": shared_ledger}
    )

    # Then: service-owned outputs remain scoped while the canonical ledger path is accepted.
    assert config.experiment_ledger == shared_ledger
    assert all(
        path.is_relative_to(config.state_root)
        for path in (
            config.report_root,
            config.policy_root,
            config.hermes_delivery_database,
            config.health_root,
            config.completion_root,
        )
    )


def test_close_config_accepts_external_shared_hermes_delivery_database(tmp_path: Path) -> None:
    # Given: the installed Hermes worker consumes one shared delivery database outside session state.
    fixture = close_fixture(tmp_path / "fixture")
    shared_delivery = tmp_path / "outputs" / "hermes" / "delivery.sqlite3"

    # When: the close service binds the same delivery database used by the intraday service.
    config = KrDayCloseServiceConfig.model_validate(
        fixture.config.model_dump(mode="python")
        | {"hermes_delivery_database": shared_delivery}
    )

    # Then: the shared delivery dependency is accepted without widening any service-owned output root.
    assert config.hermes_delivery_database == shared_delivery
    assert all(
        path.is_relative_to(config.state_root)
        for path in (config.report_root, config.policy_root, config.health_root, config.completion_root)
    )


def test_cli_bad_config_and_fixture_replay_are_compact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: one missing config and one private fixture-backed config.
    fixture = close_fixture(tmp_path / "fixture")
    assert write_kr_day_close_service_config(fixture.config_path, fixture.config)

    # When: bad input and the happy path cross the real CLI boundary.
    bad = _cli(tmp_path / "missing.json")
    first_code = cli.main(
        ("--config", str(fixture.config_path)),
        _runtime(fixture.post_close),
    )
    first = json.loads(capsys.readouterr().out)
    replay_code = cli.main(
        ("--config", str(fixture.config_path)),
        _runtime(fixture.post_close),
    )
    replay = json.loads(capsys.readouterr().out)

    # Then: bad input is compact and fixture replay remains exactly idempotent.
    assert bad.returncode == 2 and "Traceback" not in bad.stderr
    assert json.loads(bad.stdout) == {
        "complete": False,
        "reason": "config_invalid",
        "result": "blocked",
    }
    assert (first_code, replay_code) == (0, 0)
    assert first["summary_inserted"] == 1
    assert replay["summary_inserted"] == 0


def _runtime(observed_at: dt.datetime) -> KrDayCloseRuntime:
    return KrDayCloseRuntime(clock=lambda: observed_at, stage_observer=lambda _stage: None)


def _health(root: Path) -> KrDayCloseServiceHealth:
    return KrDayCloseServiceHealth.model_validate_json(
        read_private_text(root / "kr-day-close-health.json")
    )


def _cli(config: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("uv", "run", "python", str(SCRIPT), "--config", str(config)),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

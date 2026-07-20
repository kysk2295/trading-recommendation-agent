from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

import httpx2

import run_us_news_catalyst_cohort_collect
import run_us_news_catalyst_setup_observation
import run_us_news_catalyst_shadow_trial
from run_us_news_catalyst_day_session import REPORT_NAME, main
from tests.test_run_us_news_catalyst_cohort_collect import _security_store
from tests.test_us_news_catalyst_cohort_collection import _bars_response
from tests.us_news_catalyst_trial_fixtures import (
    CODE_VERSION,
    OBSERVED,
    PROJECT,
    REGISTRATION_MANIFEST,
    SESSION_DATE,
    STRATEGY_VERSION,
    projected_evidence,
    registered_ledger,
)
from trading_agent.alpaca_http import ALPACA_DATA_URL, AlpacaCredentials
from trading_agent.alpaca_news_opportunity_evidence_artifact import (
    publish_alpaca_news_opportunity_evidence,
)
from trading_agent.us_news_catalyst_day_session_manifest import (
    load_us_news_catalyst_day_session_manifest,
)
from trading_agent.us_news_catalyst_day_session_store import UsNewsCatalystDaySessionStore
from trading_agent.us_news_catalyst_opportunity_artifact import (
    publish_us_news_catalyst_opportunity_projection,
)
from trading_agent.us_news_catalyst_trial import register_us_news_catalyst_daily_trial
from trading_agent.us_news_catalyst_trial_models import UsNewsCatalystDailyTrialRegistrationRequest


def test_day_session_cli_help_is_executable() -> None:
    result = subprocess.run(
        ["uv", "run", "python", "run_us_news_catalyst_day_session.py", "--help"],
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "init" in result.stdout
    assert "tick" in result.stdout


def test_day_session_cli_initializes_and_recovers_committed_registration(tmp_path: Path) -> None:
    ledger = registered_ledger(tmp_path)
    _ = register_us_news_catalyst_daily_trial(
        ledger,
        UsNewsCatalystDailyTrialRegistrationRequest(
            strategy_version=STRATEGY_VERSION,
            code_version=CODE_VERSION,
            session_date=SESSION_DATE,
            registered_at=dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC),
        ),
    )
    manifest_path = tmp_path / "session.json"
    report_root = tmp_path / "reports"
    assert main(
        _init_args(tmp_path, manifest_path, report_root),
        clock=lambda: dt.datetime(2026, 7, 21, 12, tzinfo=dt.UTC),
    ) == 0
    manifest = load_us_news_catalyst_day_session_manifest(manifest_path)
    calls = 0

    def runner(_command: tuple[str, ...]) -> int:
        nonlocal calls
        calls += 1
        return 1

    assert main(
        ["tick", "--manifest", str(manifest_path), "--output-dir", str(report_root)],
        clock=lambda: dt.datetime(2026, 7, 21, 14, tzinfo=dt.UTC),
        runner=runner,
    ) == 0
    report = (report_root / REPORT_NAME).read_text()
    assert calls == 0
    assert manifest.paths.audit_store.is_file()
    assert "recovered" in report
    assert "order mutation: 0" in report


def test_day_session_cli_bad_input_is_redacted(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    result = main(
        [
            "tick",
            "--manifest",
            str(tmp_path / "missing-secret-name.json"),
            "--output-dir",
            str(reports),
        ]
    )

    report = (reports / REPORT_NAME).read_text()
    assert result == 1
    assert "blocked" in report
    assert "missing-secret-name" not in report


def test_day_session_cli_runs_six_domain_phases_then_replays_without_commands(
    tmp_path: Path,
) -> None:
    ledger = registered_ledger(tmp_path)
    projection, evidence = projected_evidence(ledger)
    projection_root = tmp_path / "projections"
    evidence_root = tmp_path / "evidence"
    _ = publish_us_news_catalyst_opportunity_projection(projection_root, projection)
    _ = publish_alpaca_news_opportunity_evidence(evidence_root, evidence)
    security_store = _security_store(tmp_path)
    manifest_path = tmp_path / "session.json"
    report_root = tmp_path / "reports"
    init_args = _init_args(tmp_path, manifest_path, report_root)
    init_args[init_args.index(str(tmp_path / "projections"))] = str(projection_root)
    init_args[init_args.index(str(tmp_path / "evidence"))] = str(evidence_root)
    init_args[init_args.index(str(tmp_path / "security.sqlite3"))] = str(security_store)
    assert main(
        init_args,
        clock=lambda: dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC),
    ) == 0
    manifest = load_us_news_catalyst_day_session_manifest(manifest_path)
    runtime = _FixturePhaseRunner()
    moments = (
        dt.datetime(2026, 7, 21, 13, 1, tzinfo=dt.UTC),
        OBSERVED + dt.timedelta(seconds=1),
        OBSERVED + dt.timedelta(minutes=30, seconds=1),
        OBSERVED + dt.timedelta(minutes=30, seconds=10),
        OBSERVED + dt.timedelta(minutes=30, seconds=20),
        dt.datetime(2026, 7, 21, 20, 1, tzinfo=dt.UTC),
    )
    tick_args = ["tick", "--manifest", str(manifest_path), "--output-dir", str(report_root)]

    for moment in moments:
        runtime.now = moment
        assert main(tick_args, clock=lambda moment=moment: moment, runner=runtime) == 0
    command_count = len(runtime.commands)
    runtime.now = moments[-1] + dt.timedelta(minutes=1)
    assert main(tick_args, clock=lambda: runtime.now, runner=runtime) == 0

    events = UsNewsCatalystDaySessionStore(manifest.paths.audit_store).events(manifest.session_id)
    assert len(events) == 6
    assert len(runtime.commands) == command_count == 6
    assert runtime.provider_get_count == 84
    assert all(event.status.value == "completed" for event in events)
    assert "complete" in (report_root / REPORT_NAME).read_text()


class _FixturePhaseRunner:
    def __init__(self) -> None:
        self.now = OBSERVED
        self.commands: list[tuple[str, ...]] = []
        self.provider_get_count = 0

    def __call__(self, command: tuple[str, ...]) -> int:
        self.commands.append(command)
        script = Path(command[0]).name
        argv = list(command[1:])
        if script == "run_us_news_catalyst_shadow_trial.py":
            return run_us_news_catalyst_shadow_trial.main(argv, clock=lambda: self.now)
        if script == "run_us_news_catalyst_setup_observation.py":
            return run_us_news_catalyst_setup_observation.main(argv, clock=lambda: self.now)
        if script != "run_us_news_catalyst_cohort_collect.py":
            return 1
        dependencies = run_us_news_catalyst_cohort_collect.UsNewsCatalystCollectionCliDependencies(
            clock=lambda: self.now,
            client_factory=self._client,
            credentials_loader=lambda _path: AlpacaCredentials("fixture-key", "fixture-secret"),
        )
        return run_us_news_catalyst_cohort_collect.main(argv, dependencies=dependencies)

    def _client(self) -> httpx2.Client:
        def respond(request: httpx2.Request) -> httpx2.Response:
            self.provider_get_count += 1
            return _bars_response(request)

        return httpx2.Client(
            base_url=ALPACA_DATA_URL,
            transport=httpx2.MockTransport(respond),
            follow_redirects=False,
        )


def _init_args(
    tmp_path: Path,
    manifest_path: Path,
    report_root: Path,
) -> list[str]:
    return [
        "init",
        "--registration-manifest",
        str(REGISTRATION_MANIFEST),
        "--session-date",
        SESSION_DATE.isoformat(),
        "--experiment-ledger",
        str(tmp_path / "experiment-ledger.sqlite3"),
        "--projection-root",
        str(tmp_path / "projections"),
        "--evidence-root",
        str(tmp_path / "evidence"),
        "--security-master-store",
        str(tmp_path / "security.sqlite3"),
        "--session-root",
        str(tmp_path / "day-session"),
        "--manifest",
        str(manifest_path),
        "--secret-path",
        str(tmp_path / "alpaca.env"),
        "--output-dir",
        str(report_root),
    ]

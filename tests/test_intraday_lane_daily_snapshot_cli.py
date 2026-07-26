from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

import run_intraday_lane_daily_snapshot as snapshot_cli
from tests.intraday_lane_daily_snapshot_cli_fixtures import (
    FINALIZED_AT,
    SECRET,
)
from tests.intraday_lane_daily_snapshot_cli_fixtures import (
    args as _args,
)
from tests.intraday_lane_daily_snapshot_cli_fixtures import (
    assert_redacted as _assert_redacted,
)
from tests.intraday_lane_daily_snapshot_cli_fixtures import (
    credentials as _credentials,
)
from tests.intraday_lane_daily_snapshot_cli_fixtures import (
    flat_readiness as _flat_readiness,
)
from tests.intraday_lane_daily_snapshot_cli_fixtures import (
    report as _report,
)
from tests.intraday_lane_daily_snapshot_cli_fixtures import (
    sources as _sources,
)
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.dashboard_paper_finalized_terminal import TERMINAL_FILENAME
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_runtime import PaperRuntimeReadiness
from trading_agent.paper_runtime_session import PaperLedgerReader

PROJECT = Path(__file__).parents[1]
SCRIPT = PROJECT / "run_intraday_lane_daily_snapshot.py"
_UV = shutil.which("uv")
assert _UV is not None
UV = Path(_UV)

def test_snapshot_help_is_executable_without_fixture_bypass() -> None:
    completed = subprocess.run(
        (str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )

    assert completed.returncode == 0, completed.stderr
    assert "--session-date" in completed.stdout
    assert "--execution-database" in completed.stdout
    assert "--lane-registry" in completed.stdout
    assert "fixture" not in completed.stdout.lower()


def test_snapshot_invalid_date_is_argparse_error() -> None:
    completed = subprocess.run(
        (
            str(SCRIPT),
            "missing-session",
            "--session-date",
            "not-a-date",
            "--execution-database",
            "missing-execution",
            "--lane-registry",
            "missing-registry",
            "--output-dir",
            "missing-output",
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )

    assert completed.returncode == 2
    assert "YYYY-MM-DD" in completed.stderr


@pytest.mark.parametrize("missing", ("registry", "execution", "session"))
def test_missing_local_source_blocks_before_credentials(
    tmp_path: Path,
    missing: str,
) -> None:
    sources = _sources(tmp_path)
    output = tmp_path / f"report-{missing}"
    paths = {
        "registry": sources.registry.path,
        "execution": sources.execution.path,
        "session": sources.session,
    }
    missing_path = tmp_path / f"missing-{missing}"
    paths[missing] = missing_path
    credential_calls = 0

    def credential_loader() -> AlpacaPaperCredentials:
        nonlocal credential_calls
        credential_calls += 1
        return _credentials()

    code = snapshot_cli.main(
        _args(paths["session"], paths["execution"], paths["registry"], output),
        credential_loader=credential_loader,
        probe_loader=lambda _credentials, _store: _flat_readiness(),
        clock=lambda: FINALIZED_AT,
    )

    assert code == 1
    assert credential_calls == 0
    assert not missing_path.exists()
    report = _report(output)
    assert "결과: blocked" in report
    assert "snapshot append: not_written" in report
    _assert_redacted(report, sources)


def test_fake_flat_readiness_creates_then_replays_redacted_snapshot(
    tmp_path: Path,
) -> None:
    sources = _sources(tmp_path)
    first_output = tmp_path / "report-first"
    replay_output = tmp_path / "report-replay"
    probe_calls = 0

    def probe_loader(
        credentials: AlpacaPaperCredentials,
        store: PaperLedgerReader,
    ) -> PaperRuntimeReadiness:
        nonlocal probe_calls
        probe_calls += 1
        assert credentials.key_id == "test-key"
        assert credentials.secret_key == SECRET
        assert isinstance(store, ExecutionStore)
        assert store.path == sources.execution.path
        return _flat_readiness()

    first = snapshot_cli.main(
        _args(
            sources.session,
            sources.execution.path,
            sources.registry.path,
            first_output,
        ),
        credential_loader=_credentials,
        probe_loader=probe_loader,
        clock=lambda: FINALIZED_AT,
    )
    replay = snapshot_cli.main(
        _args(
            sources.session,
            sources.execution.path,
            sources.registry.path,
            replay_output,
        ),
        credential_loader=_credentials,
        probe_loader=probe_loader,
        clock=lambda: FINALIZED_AT,
    )

    assert first == 0
    assert replay == 0
    assert probe_calls == 2
    terminal = sources.execution.path.parent / TERMINAL_FILENAME
    assert terminal.stat().st_mode & 0o777 == 0o600
    assert len(terminal.read_text(encoding="utf-8").splitlines()) == 1
    paper = collect_dashboard_snapshot_v2(
        sources.execution.path.parent.parent,
        now=FINALIZED_AT,
    ).workspaces.paper
    assert paper.state == "populated", paper.blocker_code
    assert len(sources.registry.daily_snapshots()) == 1
    first_report = _report(first_output)
    replay_report = _report(replay_output)
    assert "결과: finalized" in first_report
    assert "snapshot append: created" in first_report
    assert "snapshot append: replayed" in replay_report
    assert "미체결 주문: 0" in first_report
    assert "열린 포지션: 0" in first_report
    assert "데이터 품질 완료: 예" in first_report
    assert "allocation eligible: 아니오" in first_report
    assert "외부 Alpaca mutation: 0건" in first_report
    _assert_redacted(first_report, sources)
    _assert_redacted(replay_report, sources)


def test_broker_blocked_readiness_writes_only_generic_blocked_report(
    tmp_path: Path,
) -> None:
    sources = _sources(tmp_path)
    output = tmp_path / "blocked-report"
    blocked = replace(
        _flat_readiness(),
        market_clock=replace(_flat_readiness().market_clock, is_open=True),
        runtime_reasons=("sensitive-upstream-detail",),
    )

    code = snapshot_cli.main(
        _args(
            sources.session,
            sources.execution.path,
            sources.registry.path,
            output,
        ),
        credential_loader=_credentials,
        probe_loader=lambda _credentials, _store: blocked,
        clock=lambda: FINALIZED_AT,
    )

    assert code == 1
    assert sources.registry.daily_snapshots() == ()
    assert not (sources.execution.path.parent / TERMINAL_FILENAME).exists()
    report = _report(output)
    assert "결과: blocked" in report
    assert "snapshot append: not_written" in report
    assert "sensitive-upstream-detail" not in report
    assert "외부 Alpaca mutation: 0건" in report
    _assert_redacted(report, sources)


def _direct_execution_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{UV.parent}:/usr/bin:/bin"
    return environment

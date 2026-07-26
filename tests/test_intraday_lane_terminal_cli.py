from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import run_intraday_lane_daily_snapshot as snapshot_cli
import trading_agent.dashboard_paper_finalized_terminal_writer as terminal_writer
from tests.intraday_lane_daily_snapshot_cli_fixtures import (
    FINALIZED_AT,
    Sources,
)
from tests.intraday_lane_daily_snapshot_cli_fixtures import (
    args as _args,
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
from trading_agent.dashboard_paper_finalized_terminal import TERMINAL_FILENAME
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2


@pytest.mark.parametrize("attack", ("permissions", "symlink", "hardlink"))
def test_terminal_path_attack_blocks_real_finalizer(
    tmp_path: Path,
    attack: str,
) -> None:
    # Given a hostile terminal target at the canonical production path
    sources = _sources(tmp_path)
    terminal = sources.execution.path.parent / TERMINAL_FILENAME
    target = tmp_path / "attacker-owned"
    target.write_text("do-not-replace\n", encoding="utf-8")
    target.chmod(0o600)
    if attack == "symlink":
        terminal.symlink_to(target)
    elif attack == "hardlink":
        os.link(target, terminal)
    else:
        terminal.write_text("{}\n", encoding="utf-8")
        terminal.chmod(0o644)

    # When the actual daily finalizer reaches terminal publication
    output = tmp_path / f"report-{attack}"
    code = _run(sources, output)

    # Then it fails closed without replacing or mutating the hostile target
    assert code == 1
    assert "결과: blocked" in _report(output)
    assert target.read_text(encoding="utf-8") == "do-not-replace\n"
    assert terminal.is_symlink() if attack == "symlink" else terminal.exists()


def test_terminal_conflict_blocks_real_finalizer_replay(tmp_path: Path) -> None:
    # Given a successful production receipt changed to a valid conflicting value
    sources = _sources(tmp_path)
    assert _run(sources, tmp_path / "first") == 0
    terminal = sources.execution.path.parent / TERMINAL_FILENAME
    receipt = json.loads(terminal.read_text(encoding="utf-8"))
    receipt["recovery_snapshot_sha256"] = "f" * 64
    conflict = json.dumps(receipt, separators=(",", ":")) + "\n"
    terminal.write_text(conflict, encoding="utf-8")
    terminal.chmod(0o600)

    # When the real finalizer replays the same immutable daily snapshot
    output = tmp_path / "conflict"
    code = _run(sources, output)

    # Then conflicting terminal truth is preserved and the run is blocked
    assert code == 1
    assert terminal.read_text(encoding="utf-8") == conflict
    assert "결과: blocked" in _report(output)


def test_terminal_replace_crash_is_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the terminal atomic replace crashes after authoritative finalization
    sources = _sources(tmp_path)
    terminal = sources.execution.path.parent / TERMINAL_FILENAME

    def crash_replace(_parent: int, _name: str, _content: str) -> None:
        raise OSError("simulated replace crash")

    monkeypatch.setattr(terminal_writer, "_replace", crash_replace)

    # When the actual finalizer runs through that crash
    output = tmp_path / "crashed"
    code = _run(sources, output)

    # Then no partial terminal is visible and the run reports blocked
    assert code == 1
    assert not terminal.exists()
    assert not tuple(terminal.parent.glob(f".{TERMINAL_FILENAME}.*.writing"))
    assert "결과: blocked" in _report(output)

    # When the same production finalization is retried without the fault
    monkeypatch.undo()
    assert _run(sources, tmp_path / "recovered") == 0

    # Then the terminal and read-only dashboard projection are complete
    assert len(terminal.read_text(encoding="utf-8").splitlines()) == 1
    paper = collect_dashboard_snapshot_v2(
        sources.execution.path.parent.parent,
        now=FINALIZED_AT,
    ).workspaces.paper
    assert paper.state == "populated"


def _run(sources: Sources, output: Path) -> int:
    session = sources.session
    execution = sources.execution.path
    registry = sources.registry.path
    return snapshot_cli.main(
        _args(session, execution, registry, output),
        credential_loader=_credentials,
        probe_loader=lambda _credentials, _store: _flat_readiness(),
        clock=lambda: FINALIZED_AT,
    )

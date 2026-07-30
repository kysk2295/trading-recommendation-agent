from __future__ import annotations

import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_launchd_one_shot.py"


def test_prepared_runner_executes_payload_at_most_once(tmp_path: Path) -> None:
    # Given
    payload = tmp_path / "payload.zsh"
    counter = tmp_path / "payload-runs.txt"
    payload.write_text(
        "#!/bin/zsh\nprint -r -- run >> \"$1\"\n",
        encoding="utf-8",
    )
    payload.chmod(0o700)
    wrapper = tmp_path / "scheduled/runner.zsh"
    stdout_log = tmp_path / "scheduled/stdout.log"
    stderr_log = tmp_path / "scheduled/stderr.log"
    receipt = tmp_path / "scheduled/receipt.txt"

    prepared = subprocess.run(
        (
            "uv",
            "run",
            "--script",
            str(SCRIPT),
            "--label",
            "ai.trading-agent.pytest-one-shot",
            "--run-at",
            "1970-01-01T00:00:00+00:00",
            "--wrapper",
            str(wrapper),
            "--stdout-log",
            str(stdout_log),
            "--stderr-log",
            str(stderr_log),
            "--receipt",
            str(receipt),
            "--prepare-only",
            "--",
            str(payload),
            str(counter),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert prepared.returncode == 0

    # When
    first = subprocess.run((str(wrapper),), check=False, capture_output=True, text=True)
    second = subprocess.run((str(wrapper),), check=False, capture_output=True, text=True)

    # Then
    assert first.returncode == 0
    assert second.returncode == 0
    assert counter.read_text(encoding="utf-8").splitlines() == ["run"]
    assert receipt.read_text(encoding="utf-8").startswith("exit_code=0\n")
    assert stat.S_IMODE(wrapper.stat().st_mode) == 0o700
    assert stat.S_IMODE(stdout_log.stat().st_mode) == 0o600
    assert stat.S_IMODE(stderr_log.stat().st_mode) == 0o600
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600


def test_prepare_blocks_reusing_completed_receipt(tmp_path: Path) -> None:
    # Given
    output = tmp_path / "scheduled"
    output.mkdir()
    receipt = output / "receipt.txt"
    receipt.write_text("exit_code=0\n", encoding="utf-8")
    wrapper = output / "runner.zsh"

    # When
    completed = subprocess.run(
        (
            "uv",
            "run",
            "--script",
            str(SCRIPT),
            "--label",
            "ai.trading-agent.pytest-completed",
            "--run-at",
            "1970-01-01T00:00:00+00:00",
            "--wrapper",
            str(wrapper),
            "--stdout-log",
            str(output / "stdout.log"),
            "--stderr-log",
            str(output / "stderr.log"),
            "--receipt",
            str(receipt),
            "--prepare-only",
            "--",
            "/usr/bin/true",
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode == 1
    assert completed.stderr == '{"reason": "schedule_already_claimed", "result": "blocked"}\n'
    assert not wrapper.exists()


def test_prepare_requires_explicit_interpreter_for_env_shebang(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload.py"
    payload.write_text(
        "#!/usr/bin/env -S uv run --script\nprint('never scheduled')\n",
        encoding="utf-8",
    )
    payload.chmod(0o700)
    output = tmp_path / "scheduled"
    wrapper = output / "runner.zsh"

    completed = subprocess.run(
        (
            "uv",
            "run",
            "--script",
            str(SCRIPT),
            "--label",
            "ai.trading-agent.pytest-env-shebang",
            "--run-at",
            "1970-01-01T00:00:00+00:00",
            "--wrapper",
            str(wrapper),
            "--stdout-log",
            str(output / "stdout.log"),
            "--stderr-log",
            str(output / "stderr.log"),
            "--receipt",
            str(output / "receipt.txt"),
            "--prepare-only",
            "--",
            str(payload),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stderr == (
        '{"reason": "explicit_interpreter_required", "result": "blocked"}\n'
    )
    assert not output.exists()

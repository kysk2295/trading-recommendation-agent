from __future__ import annotations

import ast
import datetime as dt
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

import run_us_forward_shadow_tick
from tests.us_forward_shadow_support import no_signal_source, prepared_runtime, shadow_tick
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import publish_private_immutable_text


def test_tick_cli_help_exposes_only_local_research_paths() -> None:
    # Given the scheduler-facing command line surface.
    completed = subprocess.run(
        [sys.executable, "run_us_forward_shadow_tick.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    # When help is rendered, then it has local paths and no broker authority inputs.
    assert completed.returncode == 0
    assert all(name in completed.stdout for name in ("--tick", "--ledger", "--generated-artifacts"))
    assert all(term not in completed.stdout.casefold() for term in ("api-key", "account", "order", "position"))


def test_tick_cli_malformed_input_blocks_before_ledger_creation(tmp_path: Path, capsys) -> None:
    # Given a private but malformed tick snapshot and a missing ledger.
    fixture = Path(__file__).parent / "fixtures" / "day-research" / "us-forward-shadow-invalid.json"
    tick_path = tmp_path / "bad-tick.json"
    assert publish_private_immutable_text(tick_path, fixture.read_text())
    ledger_path = tmp_path / "ledger.sqlite3"

    # When one scheduler tick runs, then it returns a redacted block without mutation.
    code = run_us_forward_shadow_tick.main(_arguments(tmp_path, tick_path, ledger_path))
    output = json.loads(capsys.readouterr().out)
    assert code == 2
    assert output == {"reason_code": "input_or_runtime_blocked", "status": "blocked"}
    assert not ledger_path.exists()


def test_tick_cli_happy_path_is_restart_safe(tmp_path: Path, capsys) -> None:
    # Given a prepared real generated capsule, stored policy, and private current tick.
    services, _ = prepared_runtime(tmp_path, source=no_signal_source())
    tick = shadow_tick(services, 1, 1)
    tick_path = tmp_path / "tick.json"
    assert publish_private_immutable_text(tick_path, canonical_experiment_ledger_json(tick) + "\n")
    arguments = _arguments(tmp_path, tick_path, services.ledger.path)

    # When the CLI runs twice against the same snapshot.
    first_code = run_us_forward_shadow_tick.main(arguments, clock=_clock(tick.observed_at))
    first = json.loads(capsys.readouterr().out)
    second_code = run_us_forward_shadow_tick.main(arguments, clock=_clock(tick.observed_at))
    second = json.loads(capsys.readouterr().out)

    # Then the same trial identity is reported and no execution authority appears.
    assert first_code == second_code == 0
    assert first["results"][0]["trial_id"] == second["results"][0]["trial_id"]
    assert first["trading_authority"] is second["trading_authority"] is False
    assert services.ledger.reader().day_forward_trials()[0].events == ()


@pytest.mark.parametrize(
    "evaluation_at",
    (
        dt.datetime(2026, 8, 21, 14, 1, 30, tzinfo=dt.UTC),
        dt.datetime(2026, 8, 20, 21, 0, tzinfo=dt.UTC),
        dt.datetime(2026, 8, 20, 14, 3, 30, tzinfo=dt.UTC),
    ),
)
def test_tick_cli_rejects_noncurrent_or_stale_tick_before_ledger_creation(
    tmp_path: Path,
    capsys,
    evaluation_at: dt.datetime,
) -> None:
    services, _ = prepared_runtime(tmp_path, source=no_signal_source())
    tick = shadow_tick(services, 1, 1)
    tick_path = tmp_path / "tick.json"
    assert publish_private_immutable_text(tick_path, canonical_experiment_ledger_json(tick) + "\n")
    unopened_ledger = tmp_path / "unopened-ledger.sqlite3"

    code = run_us_forward_shadow_tick.main(
        _arguments(tmp_path, tick_path, unopened_ledger),
        clock=_clock(evaluation_at),
    )

    assert code == 2
    assert json.loads(capsys.readouterr().out) == {
        "reason_code": "input_or_runtime_blocked",
        "status": "blocked",
    }
    assert not unopened_ledger.exists()


def test_tick_runtime_modules_have_no_provider_or_mutation_imports() -> None:
    # Given every source file in the new one-tick operating vertical.
    root = Path(__file__).resolve().parents[1]
    paths = (root / "run_us_forward_shadow_tick.py", *sorted((root / "trading_agent").glob("us_forward_shadow_*.py")))

    # When imports are inspected, then provider and mutation clients are absent.
    imports = tuple(
        name
        for path in paths
        for node in ast.walk(ast.parse(path.read_text()))
        for name in _import_names(node)
    )

    assert all(
        not name.startswith(
            (
                "trading_agent.alpaca",
                "trading_agent.kis",
                "trading_agent.ls",
                "requests",
                "httpx",
                "urllib",
            )
        )
        for name in imports
    )
    assert all(
        term not in name.casefold()
        for name in imports
        for term in ("account", "order", "position", "balance", "mutation")
    )


def _arguments(root: Path, tick_path: Path, ledger_path: Path) -> list[str]:
    return [
        "--tick",
        str(tick_path),
        "--ledger",
        str(ledger_path),
        "--generated-artifacts",
        str(root / "generated"),
        "--shadow-artifacts",
        str(root / "shadow"),
        "--task-root",
        str(root / "cli-tasks"),
    ]


def _clock(value: dt.datetime) -> Callable[[], dt.datetime]:
    return lambda: value


def _import_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module is not None:
        return (node.module, *(f"{node.module}.{alias.name}" for alias in node.names))
    return ()

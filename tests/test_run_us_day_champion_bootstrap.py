from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from tests.test_us_day_champion_bootstrap import _fixture

if TYPE_CHECKING:
    from trading_agent.us_day_champion_bootstrap import UsDayChampionBootstrapRequest

ROOT = Path(__file__).parents[1]


def test_cli_help_preflight_bootstrap_and_replay(tmp_path: Path) -> None:
    # Given: a valid reviewed Champion bootstrap fixture.
    fixture = _fixture(tmp_path)
    common = _arguments(fixture.request)

    # When: an operator requests help, preflights, bootstraps, and replays.
    help_run = _run("--help")
    preflight = _run("preflight", *common)
    assert not fixture.request.version_store.exists()
    assert not fixture.request.receipt_root.exists()
    first = _run("bootstrap", *common)
    replay = _run("bootstrap", *common)

    # Then: each surface is compact and only bootstrap performs one durable registration.
    assert help_run.returncode == 0
    assert preflight.returncode == first.returncode == replay.returncode == 0
    assert json.loads(preflight.stdout)["status"] == "ready"
    assert json.loads(preflight.stdout)["version_created"] == "0"
    assert json.loads(first.stdout)["version_created"] == "1"
    assert json.loads(first.stdout)["paper_trading_enabled"] == "0"
    assert json.loads(replay.stdout)["version_created"] == "0"


def test_cli_blocks_invalid_review_without_printing_paths(tmp_path: Path) -> None:
    # Given: an unsafe review file at the public CLI boundary.
    fixture = _fixture(tmp_path)
    fixture.request.review_evidence.chmod(0o644)

    # When: bootstrap is attempted.
    completed = _run("bootstrap", *_arguments(fixture.request))

    # Then: the response is redacted, nonzero, and no store is created.
    assert completed.returncode == 2
    assert json.loads(completed.stdout) == {
        "order_authority": "0",
        "paper_trading_enabled": "0",
        "reason": "champion_bootstrap_invalid",
        "status": "blocked",
    }
    assert str(tmp_path) not in completed.stdout + completed.stderr
    assert not fixture.request.version_store.exists()


def test_cli_imports_no_paper_or_broker_authority() -> None:
    # Given / When: the read-only CLI module is imported in a fresh process.
    script = (
        "import json,sys; import run_us_day_champion_bootstrap; "
        "print(json.dumps(sorted(name for name in sys.modules if name.startswith('trading_agent.'))))"
    )
    completed = subprocess.run(
        (sys.executable, "-c", script),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    # Then: no Paper, broker, account, or execution authority module is loaded.
    loaded = json.loads(completed.stdout)
    forbidden = ("alpaca_paper", "broker", "execution_store", "paper_execution")
    assert not {name for name in loaded if any(marker in name for marker in forbidden)}


def _arguments(request: UsDayChampionBootstrapRequest) -> list[str]:
    return [
        "--strategy-manifest",
        str(request.strategy_manifest),
        "--experiment-ledger",
        str(request.experiment_ledger),
        "--version-store",
        str(request.version_store),
        "--reasoning-model-id",
        request.reasoning_model_id,
        "--prompt-policy",
        str(request.prompt_policy),
        "--tool-policy",
        str(request.tool_policy),
        "--memory-policy",
        str(request.memory_policy),
        "--review-evidence",
        str(request.review_evidence),
        "--receipt-root",
        str(request.receipt_root),
        "--created-at",
        request.created_at.isoformat(),
        "--created-session-date",
        request.created_session_date.isoformat(),
    ]


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, "run_us_day_champion_bootstrap.py", *arguments),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

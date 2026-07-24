from __future__ import annotations

import datetime as dt
import stat
import subprocess
import sys
from pathlib import Path

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kr_same_cycle_opportunity_models import (
    load_kr_same_cycle_opportunity_policy,
)
from trading_agent.kr_theme_research_registration import (
    register_kr_theme_research_manifest,
)
from trading_agent.kr_theme_research_rollover import (
    prepare_kr_theme_research_rollover,
)

PROJECT = Path(__file__).parents[1]
SCRIPT = PROJECT / "run_kr_theme_research_chain_rollover.py"
EXAMPLES = PROJECT / "examples" / "kr_theme_projection"
FIRST_CODE = "a" * 40
NEXT_CODE = "b" * 40


def test_cli_rolls_exact_previous_bundle_without_original_manifests(
    tmp_path: Path,
) -> None:
    # Given
    database = tmp_path / "experiment.sqlite3"
    ledger = ExperimentLedgerStore(database)
    opportunity_manifest = EXAMPLES / "research-registration.json"
    day_manifest = EXAMPLES / "day-research-registration.json"
    _ = register_kr_theme_research_manifest(opportunity_manifest, ledger)
    _ = register_kr_theme_research_manifest(day_manifest, ledger)
    previous = prepare_kr_theme_research_rollover(
        experiment_ledger=ledger,
        opportunity_manifest_path=opportunity_manifest,
        day_manifest_path=day_manifest,
        policy_path=EXAMPLES / "same-cycle-opportunity-policy.json",
        output_dir=tmp_path / "previous",
        code_version=FIRST_CODE,
        recorded_at=dt.datetime(2026, 7, 23, 7, tzinfo=dt.UTC),
    )
    output = tmp_path / "next"

    # When
    completed = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--previous-bundle",
            str(previous.bundle_path),
            "--database",
            str(database),
            "--output-dir",
            str(output),
            "--code-version",
            NEXT_CODE,
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode == 0
    versions = tuple(
        item.registration
        for item in ledger.multi_market_strategy_versions()
        if item.registration.code_version == NEXT_CODE
    )
    assert len(versions) == 2
    policy = load_kr_same_cycle_opportunity_policy(output / "opportunity-policy.json")
    assert policy.runtime_code_version == NEXT_CODE
    assert policy.producer_strategy_version in {item.strategy_version for item in versions}
    bundles = tuple(output.glob("kr_theme_research_rollover_*.json"))
    assert len(bundles) == 1
    assert stat.S_IMODE(bundles[0].stat().st_mode) == 0o600
    assert stat.S_IMODE((output / "kr_theme_research_rollover_ko.md").stat().st_mode) == 0o600

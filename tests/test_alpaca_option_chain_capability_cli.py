from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
import sys
from pathlib import Path

from trading_agent.data_capability_models import DataSourceId
from trading_agent.data_capability_registry import DataCapabilityRegistryStore

ROOT = Path(__file__).parents[1]
COLLECT = ROOT / "run_alpaca_option_chain_collect.py"
PROJECT = ROOT / "run_alpaca_option_chain_capability_registry.py"


def test_successful_chain_projects_local_derivatives_capability(
    tmp_path: Path,
) -> None:
    # Given one successful raw-first indicative option-chain run.
    fixture = tmp_path / "page.json"
    fixture.write_text(
        json.dumps(
            {
                "snapshots": {"AAPL260724C00200000": {}},
                "next_page_token": None,
            }
        ),
        encoding="utf-8",
    )
    database = tmp_path / "chain" / "ledger.sqlite3"
    collected = subprocess.run(
        (
            sys.executable,
            str(COLLECT),
            "--collection-id",
            "m6-capability-fixture",
            "--underlying-symbol",
            "AAPL",
            "--feed",
            "indicative",
            "--expiration-date",
            "2026-07-24",
            "--contract-type",
            "call",
            "--database",
            str(database),
            "--output-dir",
            str(tmp_path / "collect-report"),
            "--fixture-page",
            str(fixture),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert collected.returncode == 0, collected.stderr
    registry = tmp_path / "registry" / "capabilities.sqlite3"
    output = tmp_path / "capability-report"

    # When the local-only capability CLI projects that exact terminal run.
    projected = subprocess.run(
        (
            sys.executable,
            str(PROJECT),
            "--collection-id",
            "m6-capability-fixture",
            "--underlying-symbol",
            "AAPL",
            "--feed",
            "indicative",
            "--expiration-date",
            "2026-07-24",
            "--contract-type",
            "call",
            "--database",
            str(database),
            "--registry",
            str(registry),
            "--output-dir",
            str(output),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then the M6 source is resolved without provider or broker access.
    assert projected.returncode == 0, projected.stderr
    snapshot = DataCapabilityRegistryStore(registry).snapshot(
        as_of=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1),
        source_ids=(
            DataSourceId(provider="alpaca", feed="options_indicative"),
        ),
    )
    assert len(snapshot.capabilities) == 1
    assert len(snapshot.entitlements) == 1
    report = (
        output / "alpaca_option_chain_capability_registry_ko.md"
    ).read_text(encoding="utf-8")
    assert "- result: complete" in report
    assert "- network access: 0" in report
    assert "- broker, account, or order mutation: none" in report
    assert stat.S_IMODE(registry.stat().st_mode) == 0o600

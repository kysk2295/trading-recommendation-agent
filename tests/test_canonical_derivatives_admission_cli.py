from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from test_canonical_derivatives_admission import AS_OF, _stores

ROOT = Path(__file__).parents[1]


def test_canonical_derivatives_cli_exposes_query_only_replay_help() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "run_canonical_derivatives_admission.py"), "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "--contract-database" in completed.stdout
    assert "--chain-database" in completed.stdout
    assert "--as-of" in completed.stdout


def test_cli_emits_stable_json_and_replay_keeps_source_counts(tmp_path: Path) -> None:
    contract_store, chain_store, _ = _stores(tmp_path)
    command = [
        sys.executable,
        str(ROOT / "run_canonical_derivatives_admission.py"),
        "--contract-collection-id",
        "canonical-contracts",
        "--chain-collection-id",
        "canonical-chain",
        "--underlying-symbol",
        "AAPL",
        "--expiration-date",
        "2026-07-24",
        "--contract-type",
        "call",
        "--contract-database",
        str(contract_store.path),
        "--chain-database",
        str(chain_store.path),
        "--as-of",
        AS_OF.isoformat(),
    ]
    before = contract_store.counts(), chain_store.counts()

    first = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    second = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)

    assert json.loads(first.stdout) == json.loads(second.stdout)
    assert json.loads(first.stdout)["terminal_reason"] == "indicative_research_only"
    assert (contract_store.counts(), chain_store.counts()) == before

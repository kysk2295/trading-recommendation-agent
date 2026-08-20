from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from trading_agent.day_strategy_capsule_models import CapsuleAuthorityCeiling
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.research_identity_models import MarketId

PROJECT = Path(__file__).resolve().parents[1]
CLI = PROJECT / "run_day_research_contract_smoke.py"
FIXTURES = PROJECT / "tests" / "fixtures" / "day-research"


def test_contract_smoke_help_exposes_only_local_contract_inputs() -> None:
    # Given: the contract-only smoke command.
    result = subprocess.run(
        [sys.executable, str(CLI), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    # When / Then: help succeeds without provider, credential, order, or performance inputs.
    assert result.returncode == 0
    assert {"--fixture", "--database"} <= set(result.stdout.split())
    assert not {
        "--provider",
        "--credential",
        "--endpoint",
        "--account",
        "--order",
        "--return",
    } & set(result.stdout.split())


def test_contract_smoke_rejects_cross_market_fixture_before_publication(
    tmp_path: Path,
) -> None:
    # Given: a fixture that assigns a Korean capsule to a US hypothesis version.
    database = tmp_path / "blocked.sqlite3"

    # When: the contract smoke is run against the cross-market fixture.
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--fixture",
            str(FIXTURES / "cross-market.json"),
            "--database",
            str(database),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: it fails safely before creating a ledger and emits no secret-shaped diagnostics.
    output = f"{result.stdout}\n{result.stderr}".casefold()
    assert result.returncode != 0
    assert not database.exists()
    assert "traceback" not in output
    assert not {"api_key", "secret", "token", "account_id"} & set(output.split())


def test_contract_smoke_registers_independent_dual_market_capsules(
    tmp_path: Path,
) -> None:
    # Given: a valid synthetic dual-market contract fixture and a fresh ledger path.
    database = tmp_path / "contract.sqlite3"

    # When: the local smoke command runs to completion.
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--fixture",
            str(FIXTURES / "valid-dual-market.json"),
            "--database",
            str(database),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: output is sanitized and the ledger contains one shared family with split versions/capsules.
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload) == {"family_id", "markets", "status", "synthetic_fixture"}
    assert payload["status"] == "contract_only"
    assert payload["synthetic_fixture"] is True
    assert tuple(item["market_id"] for item in payload["markets"]) == (
        MarketId.KR_EQUITIES.value,
        MarketId.US_EQUITIES.value,
    )
    assert all(
        set(item) == {"capsule_id", "hypothesis_version_id", "market_id", "status"}
        and item["status"] == CapsuleAuthorityCeiling.RESEARCH_ONLY.value
        for item in payload["markets"]
    )
    store = ExperimentLedgerStore(database)
    families = store.day_hypothesis_families()
    versions = store.day_hypothesis_versions()
    capsules = store.day_strategy_capsules()
    assert len(families) == 1
    assert families[0].family.family_id == payload["family_id"]
    assert tuple(item.version.market_id for item in versions) == (
        MarketId.KR_EQUITIES,
        MarketId.US_EQUITIES,
    )
    assert tuple(item.capsule.market_id for item in capsules) == (
        MarketId.KR_EQUITIES,
        MarketId.US_EQUITIES,
    )
    assert all(
        item.capsule.authority_ceiling is CapsuleAuthorityCeiling.RESEARCH_ONLY
        and not item.capsule.trading_authority
        and not item.capsule.profitability_claim
        for item in capsules
    )


def test_contract_smoke_source_has_no_provider_or_network_imports() -> None:
    # Given: the contract-only CLI source text.
    source = CLI.read_text(encoding="utf-8")

    # When / Then: no broker, provider, HTTP, or socket module is imported.
    imports = tuple(
        line.casefold() for line in source.splitlines() if line.startswith("import ") or line.startswith("from ")
    )
    assert not any(
        token in line for line in imports for token in ("alpaca", "kis", "ls_", "http", "socket", "websocket")
    )

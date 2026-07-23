from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
import typer

import run_alpaca_option_chain_collect as chain_cli
import run_alpaca_option_contract_catalog as contract_cli
import run_alpaca_option_surface as surface_cli
from trading_agent.alpaca_option_chain_models import (
    OptionContractType,
    OptionFeed,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_exact_contract_master_and_chain_publish_ready_surface_and_replay(
    tmp_path: Path,
) -> None:
    contract_database, chain_database = _collect_inputs(tmp_path)
    output = tmp_path / "surface"

    _surface(contract_database, chain_database, output)
    first_report = _report(output)
    _surface(contract_database, chain_database, output)
    second_report = _report(output)

    artifacts = tuple(output.glob("option_surface_*.json"))
    assert len(artifacts) == 1
    artifact = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert artifact["status"] == "ready"
    assert artifact["master_contract_count"] == 1
    assert artifact["chain_snapshot_count"] == 1
    assert artifact["joined_contract_count"] == 1
    assert artifact["snapshot_coverage_bps"] == 10_000
    assert artifact["open_interest_count"] == 1
    assert artifact["quote_count"] == 1
    assert artifact["implied_volatility_count"] == 1
    assert artifact["greeks_count"] == 1
    assert len(artifact["master_run_sha256"]) == 64
    assert len(artifact["chain_run_sha256"]) == 64
    assert "result: ready" in first_report
    assert "artifact created: yes" in first_report
    assert "artifact created: no" in second_report
    assert "network access: 0" in first_report + second_report
    assert "broker, account, or order mutation: none" in first_report + second_report
    assert stat.S_IMODE(artifacts[0].stat().st_mode) == 0o600
    assert stat.S_IMODE((output / surface_cli.REPORT_NAME).stat().st_mode) == 0o600


def test_snapshot_without_exact_master_identity_is_rejected_without_artifact(
    tmp_path: Path,
) -> None:
    contract_database, _ = _collect_inputs(tmp_path)
    foreign_page = tmp_path / "foreign-chain.json"
    payload = json.loads(
        (FIXTURES / "alpaca_option_chain" / "page-001.json").read_text(
            encoding="utf-8"
        )
    )
    snapshot = payload["snapshots"].pop("AAPL260724C00200000")
    payload["snapshots"]["AAPL260724C00210000"] = snapshot
    foreign_page.write_text(json.dumps(payload), encoding="utf-8")
    chain_database = tmp_path / "foreign" / "chain.sqlite3"
    chain_cli.main(
        collection_id="m6-surface-foreign-chain",
        underlying_symbol="AAPL",
        feed=OptionFeed.INDICATIVE,
        expiration_date="2026-07-24",
        contract_type=OptionContractType.CALL,
        database=chain_database,
        output_dir=tmp_path / "foreign" / "chain-report",
        limit=1_000,
        max_pages=2,
        fixture_page=foreign_page,
    )
    output = tmp_path / "foreign" / "surface"

    with pytest.raises(typer.BadParameter):
        _surface(contract_database, chain_database, output)

    assert not tuple(output.glob("option_surface_*.json"))


def test_scope_mismatch_blocks_before_output_publication(tmp_path: Path) -> None:
    contract_database, chain_database = _collect_inputs(tmp_path)
    output = tmp_path / "wrong-scope"

    with pytest.raises(typer.BadParameter):
        surface_cli.main(
            contract_collection_id="m6-surface-contract",
            chain_collection_id="m6-surface-chain",
            underlying_symbol="AAPL",
            expiration_date="2026-07-24",
            contract_type=OptionContractType.PUT,
            feed=OptionFeed.INDICATIVE,
            contract_database=contract_database,
            chain_database=chain_database,
            output_dir=output,
            contract_limit=100,
            chain_limit=1_000,
            max_pages=2,
        )

    assert not output.exists()


def _collect_inputs(tmp_path: Path) -> tuple[Path, Path]:
    contract_database = tmp_path / "contract" / "catalog.sqlite3"
    chain_database = tmp_path / "chain" / "chain.sqlite3"
    contract_cli.main(
        collection_id="m6-surface-contract",
        underlying_symbol="AAPL",
        expiration_date="2026-07-24",
        contract_type=OptionContractType.CALL,
        database=contract_database,
        output_dir=tmp_path / "contract" / "report",
        limit=100,
        max_pages=2,
        fixture_page=FIXTURES / "alpaca_option_contract" / "page-001.json",
    )
    chain_cli.main(
        collection_id="m6-surface-chain",
        underlying_symbol="AAPL",
        feed=OptionFeed.INDICATIVE,
        expiration_date="2026-07-24",
        contract_type=OptionContractType.CALL,
        database=chain_database,
        output_dir=tmp_path / "chain" / "report",
        limit=1_000,
        max_pages=2,
        fixture_page=FIXTURES / "alpaca_option_chain" / "page-001.json",
    )
    return contract_database, chain_database


def _surface(
    contract_database: Path,
    chain_database: Path,
    output: Path,
) -> None:
    surface_cli.main(
        contract_collection_id="m6-surface-contract",
        chain_collection_id="m6-surface-chain",
        underlying_symbol="AAPL",
        expiration_date="2026-07-24",
        contract_type=OptionContractType.CALL,
        feed=OptionFeed.INDICATIVE,
        contract_database=contract_database,
        chain_database=chain_database,
        output_dir=output,
        contract_limit=100,
        chain_limit=1_000,
        max_pages=2,
    )


def _report(output: Path) -> str:
    return (output / surface_cli.REPORT_NAME).read_text(encoding="utf-8")

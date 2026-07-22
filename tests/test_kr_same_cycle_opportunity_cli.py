from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
from pathlib import Path

import pytest

import run_kr_same_cycle_opportunity
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_theme_research_registration import (
    kr_theme_strategy_version,
    register_kr_theme_research_manifest,
)
from trading_agent.kr_theme_store import KrThemeStore
from trading_agent.signal_contract_models import OpportunitySnapshot

ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "kr_same_cycle"
CYCLE_ID = "kr-live-opportunity-cli-001"
COLLECTION_DATE = "2026-07-16"
CODE_VERSION = "kr-live-opportunity-cli-code-v1"
KST = dt.timezone(dt.timedelta(hours=9))


def test_fixture_cli_collects_and_projects_same_cycle_opportunity_with_exact_replay(
    tmp_path: Path,
) -> None:
    # Given: registered KR Opportunity research and a same-cycle operating policy.
    paths = _paths(tmp_path)
    _register(paths["ledger"], tmp_path)
    policy = _write_policy(tmp_path)
    argv = _argv(paths, policy)

    # When: the whole read-only collection-to-Opportunity cycle runs twice.
    first = run_kr_same_cycle_opportunity.main(
        argv,
        clock=lambda: dt.datetime(2026, 7, 16, 10, 2, 30, tzinfo=KST),
    )
    second = run_kr_same_cycle_opportunity.main(
        argv,
        clock=lambda: dt.datetime(2026, 7, 16, 15, 0, tzinfo=KST),
    )

    # Then: one causal Opportunity and one private immutable run survive replay.
    outbox = paths["projection"] / "opportunities.v1.jsonl"
    opportunities = tuple(
        OpportunitySnapshot.model_validate_json(line) for line in outbox.read_text(encoding="utf-8").splitlines()
    )
    assert first == 0
    assert second == 0
    assert len(opportunities) == 1
    assert opportunities[0].candidates[0].symbol == "005930"
    assert opportunities[0].observed_at == KrThemeStore(paths["database"]).cycles()[0].completed_at
    assert stat.S_IMODE(outbox.stat().st_mode) == 0o600
    assert len(tuple(paths["runs"].glob("*/projection-run.json"))) == 1
    summary = paths["output"] / "kr_same_cycle_opportunity_ko.md"
    assert stat.S_IMODE(summary.stat().st_mode) == 0o600
    assert "result: ready" in summary.read_text(encoding="utf-8")
    assert "opportunity count: 1" in summary.read_text(encoding="utf-8")
    delivery = HermesDeliveryStore(paths["delivery"])
    assert len(delivery.events()) == 1
    assert delivery.events()[0].kind is HermesDeliveryKind.WATCH


def test_unregistered_policy_blocks_before_collection_store_creation(tmp_path: Path) -> None:
    # Given: a syntactically valid policy without experiment authority.
    paths = _paths(tmp_path)
    with ExperimentLedgerStore(paths["ledger"]).writer():
        pass
    policy = _write_policy(tmp_path)

    # When: the operating cycle is requested.
    result = run_kr_same_cycle_opportunity.main(
        _argv(paths, policy),
        clock=lambda: dt.datetime(2026, 7, 16, 10, 2, 30, tzinfo=KST),
    )

    # Then: it fails before any provider-backed collection ledger is opened.
    assert result == 1
    assert not paths["database"].exists()
    assert not paths["delivery"].exists()
    assert "result: blocked" in (paths["output"] / "kr_same_cycle_opportunity_ko.md").read_text(encoding="utf-8")


def test_complete_cycle_without_candidate_delivers_no_opportunity_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    paths = _paths(tmp_path)
    _register(paths["ledger"], tmp_path)
    policy = _write_policy(tmp_path)
    monkeypatch.setattr(run_kr_same_cycle_opportunity.run_kr_theme_projection, "main", lambda **_values: None)

    # When
    first = run_kr_same_cycle_opportunity.main(
        _argv(paths, policy),
        clock=lambda: dt.datetime(2026, 7, 16, 10, 2, 30, tzinfo=KST),
    )
    second = run_kr_same_cycle_opportunity.main(
        _argv(paths, policy),
        clock=lambda: dt.datetime(2026, 7, 16, 15, 0, tzinfo=KST),
    )

    # Then
    events = HermesDeliveryStore(paths["delivery"]).events()
    assert first == 0
    assert second == 0
    assert len(events) == 1
    assert events[0].kind is HermesDeliveryKind.NO_RECOMMENDATION
    assert "result: no_opportunity" in (paths["output"] / "kr_same_cycle_opportunity_ko.md").read_text(
        encoding="utf-8"
    )


def test_historical_production_request_blocks_before_collector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: registered research but a non-fixture request for another KST date.
    paths = _paths(tmp_path)
    _register(paths["ledger"], tmp_path)
    policy = _write_policy(tmp_path)
    argv = _argv(paths, policy)[:-2]

    def reject_collection(**_values: str | None) -> None:
        raise AssertionError("historical production request invoked collector")

    monkeypatch.setattr(
        run_kr_same_cycle_opportunity.run_kr_same_cycle_collect,
        "main",
        reject_collection,
    )

    # When: the operator requests that historical date without fixture mode.
    result = run_kr_same_cycle_opportunity.main(
        argv,
        clock=lambda: dt.datetime(2026, 7, 17, 10, 2, 30, tzinfo=KST),
    )

    # Then: no provider or collection ledger is opened.
    assert result == 1
    assert not paths["database"].exists()


def test_help_exposes_no_account_or_order_surface() -> None:
    # Given / When: an operator inspects the real CLI.
    completed = subprocess.run(
        [str(ROOT / "run_kr_same_cycle_opportunity.py"), "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: only read-only collection and local projection controls are exposed.
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0
    for option in (
        "--collection-cycle-id",
        "--collection-date",
        "--policy",
        "--database",
        "--experiment-ledger",
        "--delivery-database",
        "--fixture-root",
    ):
        assert option in output
    for forbidden in ("--account", "--order", "--broker", "--arm", "--url"):
        assert forbidden not in output


def _paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "database": tmp_path / "kr-theme.sqlite3",
        "ledger": tmp_path / "experiment.sqlite3",
        "delivery": tmp_path / "delivery.sqlite3",
        "collection": tmp_path / "collection",
        "projection": tmp_path / "projection",
        "runs": tmp_path / "runs",
        "output": tmp_path / "operator",
    }


def _argv(paths: dict[str, Path], policy: Path) -> list[str]:
    return [
        "--collection-cycle-id",
        CYCLE_ID,
        "--collection-date",
        COLLECTION_DATE,
        "--policy",
        str(policy),
        "--database",
        str(paths["database"]),
        "--experiment-ledger",
        str(paths["ledger"]),
        "--delivery-database",
        str(paths["delivery"]),
        "--collection-output-dir",
        str(paths["collection"]),
        "--run-root",
        str(paths["runs"]),
        "--projection-output-dir",
        str(paths["projection"]),
        "--output-dir",
        str(paths["output"]),
        "--fixture-root",
        str(FIXTURES),
    ]


def _write_policy(tmp_path: Path) -> Path:
    rules = json.loads((ROOT / "examples" / "kr_theme_projection" / "keyword-rules.json").read_text(encoding="utf-8"))
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "producer_strategy_version": kr_theme_strategy_version(CODE_VERSION),
                "runtime_code_version": CODE_VERSION,
                "validity_seconds": 300,
                "maximum_cycle_age_seconds": 300,
                "rules": rules,
            }
        ),
        encoding="utf-8",
    )
    return path


def _register(ledger_path: Path, tmp_path: Path) -> None:
    manifest = json.loads(
        (ROOT / "examples" / "kr_theme_projection" / "research-registration.json").read_text(encoding="utf-8")
    )
    manifest["strategy_version"] = kr_theme_strategy_version(CODE_VERSION)
    manifest["code_version"] = CODE_VERSION
    manifest["source_registered_at"] = "2026-07-16T08:00:00+09:00"
    manifest["ledger_recorded_at"] = "2026-07-16T08:00:00+09:00"
    path = tmp_path / "registration.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    _ = register_kr_theme_research_manifest(path, ExperimentLedgerStore(ledger_path))

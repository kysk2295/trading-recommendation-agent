from __future__ import annotations

import datetime as dt
import json
import stat
from pathlib import Path

import pytest

import run_kr_same_cycle_collect
import trading_agent.kr_same_cycle_opportunity_bundle as bundle_module
from trading_agent.kr_same_cycle_opportunity_run import (
    InvalidKrSameCycleOpportunityRunError,
    KrSameCycleOpportunityPolicy,
    KrSameCycleOpportunityPreparation,
    load_kr_same_cycle_opportunity_policy,
    prepare_kr_same_cycle_opportunity_run,
)
from trading_agent.kr_theme_research_registration import kr_theme_strategy_version
from trading_agent.kr_theme_store import KrThemeStore

FIXTURES = Path(__file__).parent / "fixtures" / "kr_same_cycle"
CYCLE_ID = "kr-live-opportunity-001"
COLLECTION_DATE = dt.date(2026, 7, 16)
CODE_VERSION = "kr-live-opportunity-test-code-v1"


def test_preparation_materializes_private_exact_run_and_replays_after_freshness_window(
    tmp_path: Path,
) -> None:
    # Given: one complete same-cycle fixture and a registered-shape policy.
    store = _collected_store(tmp_path)
    policy = _policy()
    root = tmp_path / "runs"
    completed_at = store.cycles()[0].completed_at
    request = KrSameCycleOpportunityPreparation(
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        prepared_at=completed_at + dt.timedelta(seconds=30),
        run_root=root,
    )

    # When: the run is prepared and then replayed after its live freshness window.
    first = prepare_kr_same_cycle_opportunity_run(store, request, policy)
    replay = prepare_kr_same_cycle_opportunity_run(
        store,
        KrSameCycleOpportunityPreparation(
            collection_cycle_id=CYCLE_ID,
            collection_date=COLLECTION_DATE,
            prepared_at=completed_at + dt.timedelta(hours=5),
            run_root=root,
        ),
        policy,
    )

    # Then: one immutable bundle remains usable for exact recovery.
    assert first.replayed is False
    assert replay.replayed is True
    assert replay.run_manifest == first.run_manifest
    assert replay.loaded == first.loaded
    assert stat.S_IMODE(first.run_manifest.parent.stat().st_mode) == 0o700
    for name in ("policy.json", "keyword-rules.json", "projection-run.json"):
        assert stat.S_IMODE((first.run_manifest.parent / name).stat().st_mode) == 0o600


def test_preparation_rejects_stale_first_projection_without_creating_bundle(
    tmp_path: Path,
) -> None:
    # Given: a complete cycle whose first projection is outside the policy window.
    store = _collected_store(tmp_path)
    root = tmp_path / "runs"
    completed_at = store.cycles()[0].completed_at
    request = KrSameCycleOpportunityPreparation(
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        prepared_at=completed_at + dt.timedelta(minutes=6),
        run_root=root,
    )

    # When / Then: stale evidence fails closed before a run artifact exists.
    with pytest.raises(InvalidKrSameCycleOpportunityRunError):
        prepare_kr_same_cycle_opportunity_run(store, request, _policy())
    assert not root.exists()


def test_policy_loader_rejects_symlink(tmp_path: Path) -> None:
    # Given: valid policy bytes exposed through a symlink.
    target = tmp_path / "policy.json"
    target.write_text(_policy().model_dump_json(), encoding="utf-8")
    alias = tmp_path / "alias.json"
    alias.symlink_to(target)

    # When / Then: the operator boundary refuses the alias.
    with pytest.raises(InvalidKrSameCycleOpportunityRunError):
        load_kr_same_cycle_opportunity_policy(alias)


def test_bundle_write_failure_never_exposes_partial_final_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a fresh cycle and a filesystem fault after the first staged file.
    store = _collected_store(tmp_path)
    root = tmp_path / "runs"
    request = KrSameCycleOpportunityPreparation(
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        prepared_at=store.cycles()[0].completed_at + dt.timedelta(seconds=30),
        run_root=root,
    )
    original = bundle_module._write_exact_private
    writes = 0

    def fail_second_write(path: Path, content: bytes) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError
        original(path, content)

    monkeypatch.setattr(bundle_module, "_write_exact_private", fail_second_write)

    # When: materialization fails before the bundle is complete.
    with pytest.raises(InvalidKrSameCycleOpportunityRunError):
        prepare_kr_same_cycle_opportunity_run(store, request, _policy())

    # Then: the deterministic final bundle path was never published.
    assert not tuple(root.glob("kr-opportunity-*"))


def _collected_store(tmp_path: Path) -> KrThemeStore:
    database = tmp_path / "kr-theme.sqlite3"
    run_kr_same_cycle_collect.main(
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE.isoformat(),
        database=str(database),
        output_dir=str(tmp_path / "collection"),
        fixture_root=str(FIXTURES),
    )
    return KrThemeStore(database)


def _policy() -> KrSameCycleOpportunityPolicy:
    rules = json.loads(
        (Path(__file__).parents[1] / "examples" / "kr_theme_projection" / "keyword-rules.json").read_text(
            encoding="utf-8"
        )
    )
    return KrSameCycleOpportunityPolicy.model_validate(
        {
            "schema_version": 1,
            "producer_strategy_version": kr_theme_strategy_version(CODE_VERSION),
            "runtime_code_version": CODE_VERSION,
            "validity_seconds": 300,
            "maximum_cycle_age_seconds": 300,
            "rules": rules,
        }
    )

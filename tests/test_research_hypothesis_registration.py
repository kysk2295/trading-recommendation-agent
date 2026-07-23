from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.research_hypothesis_registration import (
    InvalidResearchHypothesisManifestError,
    register_research_hypothesis_manifest,
)
from trading_agent.source_driven_hypothesis_queue import (
    HypothesisQueueRoute,
    project_source_driven_hypothesis_queue,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "research" / "us-swing-new-high-rvol-v1.json"
INTRADAY_SOURCE_MANIFESTS = (
    ROOT / "examples" / "research" / "us-vwap-reclaim-source-v2.json",
    ROOT / "examples" / "research" / "us-hod-breakout-source-v2.json",
    ROOT / "examples" / "research" / "us-gap-and-go-source-v2.json",
)


def test_registers_source_bound_us_swing_hypothesis_and_replays(tmp_path: Path) -> None:
    database = tmp_path / "research.sqlite3"
    ledger = ExperimentLedgerStore(database)

    first = register_research_hypothesis_manifest(EXAMPLE, ledger)
    replay = register_research_hypothesis_manifest(EXAMPLE, ledger)
    reader = ExperimentLedgerReader(database)

    assert (first.sources_created, first.cards_created) == (2, 1)
    assert (replay.sources_created, replay.cards_created) == (0, 0)
    assert len(reader.research_sources()) == 2
    assert len(reader.research_hypothesis_cards()) == 1
    assert len(reader.hypotheses()) == 1
    assert reader.strategy_versions() == ()
    assert reader.trials() == ()


def test_invalid_manifest_does_not_open_or_create_ledger(tmp_path: Path) -> None:
    manifest = tmp_path / "invalid.json"
    manifest.write_text("{not json", encoding="utf-8")
    database = tmp_path / "research.sqlite3"

    with pytest.raises(InvalidResearchHypothesisManifestError):
        _ = register_research_hypothesis_manifest(manifest, ExperimentLedgerStore(database))

    assert not database.exists()


def test_manifest_rejects_source_recorded_after_scope_preregistration(tmp_path: Path) -> None:
    payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    payload["research_sources"][0]["retrieved_at"] = "2026-07-16T20:16:00Z"
    payload["research_sources"][0]["ledger_recorded_at"] = "2026-07-16T20:16:00Z"
    manifest = tmp_path / "late-source.json"
    database = tmp_path / "research.sqlite3"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(InvalidResearchHypothesisManifestError):
        _ = register_research_hypothesis_manifest(manifest, ExperimentLedgerStore(database))

    assert not database.exists()


def test_three_intraday_source_cards_share_sources_and_route_to_design(tmp_path: Path) -> None:
    database = tmp_path / "research.sqlite3"
    ledger = ExperimentLedgerStore(database)

    results = tuple(register_research_hypothesis_manifest(manifest, ledger) for manifest in INTRADAY_SOURCE_MANIFESTS)
    reader = ExperimentLedgerReader(database)
    queue = project_source_driven_hypothesis_queue(reader)

    assert tuple(result.sources_created for result in results) == (2, 0, 0)
    assert tuple(result.cards_created for result in results) == (1, 1, 1)
    assert len(reader.research_sources()) == 2
    assert tuple(item.hypothesis_id for item in queue.snapshot.items) == (
        "H-MOM-VWAP-SOURCE-002",
        "H-MOM-HOD-SOURCE-002",
        "H-MOM-GAP-SOURCE-002",
    )
    assert all(item.route is HypothesisQueueRoute.STRATEGY_DESIGN for item in queue.snapshot.items)

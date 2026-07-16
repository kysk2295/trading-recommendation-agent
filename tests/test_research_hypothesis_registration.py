from __future__ import annotations

from pathlib import Path

import pytest

from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.research_hypothesis_registration import (
    InvalidResearchHypothesisManifestError,
    register_research_hypothesis_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "research" / "us-swing-new-high-rvol-v1.json"


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

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.test_us_opportunity_scanner_projection import FOUNDATION, _opportunity
from trading_agent.data_foundation_manifest import load_data_foundation_manifest
from trading_agent.research_evidence_models import ClaimCorroborationStatus
from trading_agent.us_opportunity_scanner_projection import UsOpportunityScannerProjector
from trading_agent.us_opportunity_scanner_store import UsOpportunityScannerStore
from trading_agent.us_scanner_research_projection import (
    UsScannerResearchProjectionError,
    project_us_scanner_research_evidence,
)


def test_verified_scanner_candidate_projects_unconfirmed_selection_claim(
    tmp_path: Path,
) -> None:
    store = _scanner_store(tmp_path)

    model = project_us_scanner_research_evidence(store.path)

    assert model.source_event_count == 1
    assert model.extraction_count == 1
    assert len(model.claims) == 1
    claim = model.claims[0]
    assert claim.claim_key == "us.scanner.ranking_momentum.selected"
    assert claim.claim_kind == "scanner.candidate_selection"
    assert claim.reporting_evidence_count == 1
    assert claim.corroboration_status is ClaimCorroborationStatus.UNCONFIRMED
    assert claim.source_ids == ("internal/us_opportunity",)


def test_changed_security_master_lineage_fails_before_claim_projection(
    tmp_path: Path,
) -> None:
    store = _scanner_store(tmp_path)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER us_opportunity_scanner_projections_no_update")
        connection.execute(
            "UPDATE us_opportunity_scanner_projections SET security_master_id = 'tampered-security-master'"
        )
        connection.commit()

    with pytest.raises(UsScannerResearchProjectionError, match="blocked"):
        _ = project_us_scanner_research_evidence(store.path)


def _scanner_store(tmp_path: Path) -> UsOpportunityScannerStore:
    store = UsOpportunityScannerStore(tmp_path / "scanner.sqlite3")
    _ = UsOpportunityScannerProjector(store, tmp_path / "canonical").project(
        _opportunity(),
        load_data_foundation_manifest(FOUNDATION),
    )
    return store

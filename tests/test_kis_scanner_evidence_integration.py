from __future__ import annotations

from pathlib import Path

from tests.test_us_opportunity_scanner_projection import FOUNDATION, _opportunity
from trading_agent.kis_research_projection import (
    ResearchProjectionOptions,
    configure_research_projection,
    project_opportunity_research_input,
)
from trading_agent.research_evidence_artifact import load_research_evidence_artifact
from trading_agent.research_evidence_models import ClaimCorroborationStatus


def test_committed_scanner_projection_writes_and_replays_evidence_artifact(
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "research-evidence"
    config = configure_research_projection(
        ResearchProjectionOptions(
            str(FOUNDATION),
            str(tmp_path / "scanner.sqlite3"),
            str(tmp_path / "canonical"),
        )
    )
    assert config is not None

    first = project_opportunity_research_input(_opportunity(), config)
    second = project_opportunity_research_input(_opportunity(), config)

    assert first == second
    artifacts = tuple(evidence_root.glob("research_evidence_*.json"))
    assert len(artifacts) == 1
    model = load_research_evidence_artifact(artifacts[0])
    assert model.claims[0].corroboration_status is ClaimCorroborationStatus.UNCONFIRMED

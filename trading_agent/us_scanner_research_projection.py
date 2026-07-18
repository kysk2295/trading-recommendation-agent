from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import override

from trading_agent.research_evidence_models import ResearchEvidenceReadModel
from trading_agent.research_evidence_read_model import (
    ResearchEvidenceReadModelError,
    build_research_evidence_read_model,
)
from trading_agent.us_scanner_candidate_extraction import (
    UsScannerCandidateExtractionError,
    extract_us_scanner_candidate_claims,
)
from trading_agent.us_scanner_research_source import (
    UsScannerResearchSourceError,
    load_latest_us_scanner_research_source,
)


class UsScannerResearchProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "US scanner research projection is blocked"


def project_us_scanner_research_evidence(
    scanner_store: Path,
) -> ResearchEvidenceReadModel:
    try:
        source = load_latest_us_scanner_research_source(scanner_store)
        claims = extract_us_scanner_candidate_claims(source)
        return build_research_evidence_read_model(
            source.events,
            claims,
            as_of=source.opportunity.observed_at,
            current_window=dt.timedelta(hours=1),
            baseline_window=dt.timedelta(days=1),
            burst_threshold_bps=20_000,
        )
    except (
        OSError,
        ResearchEvidenceReadModelError,
        TypeError,
        UsScannerCandidateExtractionError,
        UsScannerResearchSourceError,
        ValueError,
    ):
        raise UsScannerResearchProjectionError from None


__all__ = (
    "UsScannerResearchProjectionError",
    "project_us_scanner_research_evidence",
)

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import override

from trading_agent.canonical_dataset_event_reader import replay_canonical_dataset_events
from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplayError
from trading_agent.research_evidence_models import ResearchEvidenceReadModel
from trading_agent.research_evidence_read_model import (
    ResearchEvidenceReadModelError,
    build_research_evidence_read_model,
)
from trading_agent.us_feature_evidence_models import UsFeatureEvidenceBinding
from trading_agent.us_sip_typed_feature_extraction import (
    UsSipTypedFeatureExtractionError,
    extract_us_sip_typed_feature_claims,
)


class UsSipResearchEvidenceProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "US SIP research evidence projection is blocked"


def project_us_sip_research_evidence(
    bindings: tuple[UsFeatureEvidenceBinding, ...],
    canonical_root: Path,
    *,
    minimum_rvol_bps: int,
) -> tuple[ResearchEvidenceReadModel, ...]:
    try:
        if (
            type(bindings) is not tuple
            or not bindings
            or any(type(item) is not UsFeatureEvidenceBinding for item in bindings)
            or type(minimum_rvol_bps) is not int
            or not 1 <= minimum_rvol_bps <= 100_000
        ):
            raise UsSipResearchEvidenceProjectionError
        ordered = tuple(sorted(bindings, key=lambda item: item.snapshot.instrument_id))
        if len({item.snapshot.instrument_id for item in ordered}) != len(ordered):
            raise UsSipResearchEvidenceProjectionError
        return tuple(_project_binding(item, canonical_root, minimum_rvol_bps) for item in ordered)
    except (
        CanonicalDatasetReplayError,
        OSError,
        ResearchEvidenceReadModelError,
        TypeError,
        UsSipTypedFeatureExtractionError,
        ValueError,
    ):
        raise UsSipResearchEvidenceProjectionError from None


def _project_binding(
    binding: UsFeatureEvidenceBinding,
    canonical_root: Path,
    minimum_rvol_bps: int,
) -> ResearchEvidenceReadModel:
    snapshot = binding.snapshot
    dataset = _dataset_directory(canonical_root, snapshot.identity.dataset_id)
    _replay, events = replay_canonical_dataset_events(dataset)
    claims = extract_us_sip_typed_feature_claims(
        snapshot,
        dataset,
        minimum_rvol_bps=minimum_rvol_bps,
    )
    return build_research_evidence_read_model(
        events,
        claims,
        as_of=snapshot.observed_at,
        current_window=dt.timedelta(hours=1),
        baseline_window=dt.timedelta(days=1),
        burst_threshold_bps=20_000,
    )


def _dataset_directory(root: Path, dataset_id: str) -> Path:
    candidate = root.expanduser().absolute()
    matches = tuple(candidate.rglob(f"dataset_id={dataset_id}"))
    if len(matches) != 1:
        raise UsSipResearchEvidenceProjectionError
    return matches[0]


__all__ = (
    "UsSipResearchEvidenceProjectionError",
    "project_us_sip_research_evidence",
)

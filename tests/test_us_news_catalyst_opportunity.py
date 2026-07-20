from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from trading_agent.alpaca_news_coverage_models import (
    AlpacaNewsCoverageAssessment,
    AlpacaNewsCoverageManifest,
    AlpacaNewsCoverageSlice,
    AlpacaNewsCoverageSliceStatus,
)
from trading_agent.alpaca_news_models import AlpacaNewsRequest
from trading_agent.alpaca_news_opportunity_evidence import (
    AlpacaNewsEvidenceObservation,
    AlpacaNewsOpportunityEvidenceBundle,
    AlpacaNewsOpportunityEvidenceSnapshot,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.signal_contract_models import EvidenceRef, SourceCoverage
from trading_agent.us_news_catalyst_opportunity import (
    UsNewsCatalystProjectionStatus,
    project_registered_us_news_catalyst_opportunity,
)
from trading_agent.us_news_catalyst_opportunity_artifact import (
    load_us_news_catalyst_opportunity_projection,
    publish_us_news_catalyst_opportunity_projection,
)
from trading_agent.us_news_catalyst_research_registration import (
    InvalidUsNewsCatalystResearchRegistrationError,
    UsNewsCatalystProjectionAuthorityRequest,
    register_us_news_catalyst_research_manifest,
    us_news_catalyst_strategy_version,
)

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT / "examples" / "us_news_catalyst" / "research-registration.json"
CODE_VERSION = "us-news-catalyst-baseline-fixture-v1"
OBSERVED = dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC)


def test_registered_projection_ranks_recent_provider_events_deterministically(
    tmp_path: Path,
) -> None:
    ledger = _registered_ledger(tmp_path)
    bundle = _bundle(
        aapl_updates=(OBSERVED - dt.timedelta(seconds=60), OBSERVED - dt.timedelta(seconds=90)),
        msft_updates=(OBSERVED - dt.timedelta(seconds=10),),
    )
    request = _authority(bundle)

    first = project_registered_us_news_catalyst_opportunity(bundle, ledger, request)
    second = project_registered_us_news_catalyst_opportunity(bundle, ledger, request)

    assert first == second
    assert first.status is UsNewsCatalystProjectionStatus.RANKED
    assert first.snapshot is not None
    assert tuple(item.symbol for item in first.snapshot.candidates) == ("AAPL", "MSFT")
    assert first.snapshot.producer_strategy_version == request.strategy_version
    assert first.snapshot.valid_until == OBSERVED + dt.timedelta(minutes=5)
    assert first.snapshot.source_coverage[0].record_count == 3
    aapl_features = {item.name: item.value for item in first.snapshot.candidates[0].features}
    assert aapl_features["recent_article_count"] == "2"
    assert aapl_features["latest_provider_age_seconds"] == "60"
    assert "TSLA" not in tuple(item.symbol for item in first.snapshot.candidates)


def test_projection_returns_terminal_no_candidates_for_stale_or_zero_news(
    tmp_path: Path,
) -> None:
    ledger = _registered_ledger(tmp_path)
    bundle = _bundle(
        aapl_updates=(OBSERVED - dt.timedelta(seconds=301),),
        msft_updates=(),
    )

    result = project_registered_us_news_catalyst_opportunity(
        bundle,
        ledger,
        _authority(bundle),
    )

    assert result.status is UsNewsCatalystProjectionStatus.NO_CANDIDATES
    assert result.snapshot is None
    assert result.eligible_symbol_count == 0


def test_projection_fails_closed_without_exact_registered_version(tmp_path: Path) -> None:
    bundle = _bundle(aapl_updates=(OBSERVED - dt.timedelta(seconds=10),), msft_updates=())
    ledger = ExperimentLedgerStore(tmp_path / "experiment-ledger.sqlite3")
    with ledger.writer():
        pass

    with pytest.raises(InvalidUsNewsCatalystResearchRegistrationError):
        _ = project_registered_us_news_catalyst_opportunity(bundle, ledger, _authority(bundle))


def test_projection_artifact_is_private_idempotent_and_tamper_evident(
    tmp_path: Path,
) -> None:
    ledger = _registered_ledger(tmp_path)
    bundle = _bundle(aapl_updates=(OBSERVED - dt.timedelta(seconds=10),), msft_updates=())
    projection = project_registered_us_news_catalyst_opportunity(bundle, ledger, _authority(bundle))
    root = tmp_path / "projection"

    path, created = publish_us_news_catalyst_opportunity_projection(root, projection)
    replay_path, replay_created = publish_us_news_catalyst_opportunity_projection(root, projection)

    assert created is True
    assert replay_created is False
    assert replay_path == path
    assert load_us_news_catalyst_opportunity_projection(path) == projection
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="projection"):
        _ = load_us_news_catalyst_opportunity_projection(path)


def test_projection_identity_binds_ranked_candidate_content(tmp_path: Path) -> None:
    ledger = _registered_ledger(tmp_path)
    bundle = _bundle(aapl_updates=(OBSERVED - dt.timedelta(seconds=10),), msft_updates=())
    projection = project_registered_us_news_catalyst_opportunity(bundle, ledger, _authority(bundle))
    path, _ = publish_us_news_catalyst_opportunity_projection(tmp_path / "projection", projection)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["snapshot"]["candidates"][0]["score"] = "999"
    path.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="projection"):
        _ = load_us_news_catalyst_opportunity_projection(path)


def _registered_ledger(tmp_path: Path) -> ExperimentLedgerStore:
    ledger = ExperimentLedgerStore(tmp_path / "experiment-ledger.sqlite3")
    _ = register_us_news_catalyst_research_manifest(REGISTRATION, ledger)
    return ledger


def _authority(
    bundle: AlpacaNewsOpportunityEvidenceBundle,
) -> UsNewsCatalystProjectionAuthorityRequest:
    return UsNewsCatalystProjectionAuthorityRequest(
        strategy_version=us_news_catalyst_strategy_version(CODE_VERSION),
        code_version=CODE_VERSION,
        projected_at=bundle.assessment.assessed_at,
    )


def _bundle(
    *,
    aapl_updates: tuple[dt.datetime, ...],
    msft_updates: tuple[dt.datetime, ...],
) -> AlpacaNewsOpportunityEvidenceBundle:
    request = AlpacaNewsRequest(
        collection_id="us-news-catalyst-fixture",
        symbols=("AAPL", "MSFT", "TSLA"),
        start_at=OBSERVED - dt.timedelta(hours=1),
        end_at=OBSERVED - dt.timedelta(seconds=1),
        limit=50,
        max_pages=2,
    )
    manifest = AlpacaNewsCoverageManifest(
        universe_id="us_news_catalyst_fixture",
        cutoff_at=OBSERVED,
        requests=(request,),
    )
    all_updates = aapl_updates + msft_updates
    assessment = AlpacaNewsCoverageAssessment(
        manifest_id=manifest.manifest_id,
        universe_id=manifest.universe_id,
        assessed_at=OBSERVED,
        slices=(
            AlpacaNewsCoverageSlice(
                request_id=request.request_id,
                status=AlpacaNewsCoverageSliceStatus.SUCCESS,
                run_id="f" * 64,
                completed_at=OBSERVED,
                page_count=1,
                article_count=len(all_updates),
                latest_event_at=max(all_updates, default=None),
                failure_code=None,
            ),
        ),
        declared_symbol_count=3,
        successful_symbol_count=3,
        completeness_bps=10_000,
        accepted_article_count=len(all_updates),
        latest_event_at=max(all_updates, default=None),
    )
    snapshots = tuple(
        _snapshot(manifest, assessment, symbol, updates, index)
        for index, (symbol, updates) in enumerate(
            (("AAPL", aapl_updates), ("MSFT", msft_updates), ("TSLA", ())),
            start=1,
        )
    )
    return AlpacaNewsOpportunityEvidenceBundle(
        manifest=manifest,
        assessment=assessment,
        snapshots=snapshots,
    )


def _snapshot(
    manifest: AlpacaNewsCoverageManifest,
    assessment: AlpacaNewsCoverageAssessment,
    symbol: str,
    updates: tuple[dt.datetime, ...],
    symbol_index: int,
) -> AlpacaNewsOpportunityEvidenceSnapshot:
    observations = tuple(
        AlpacaNewsEvidenceObservation(
            event_id=f"{symbol_index:x}{event_index:063x}",
            receipt_id=f"{symbol_index + 8:x}{event_index:063x}",
            symbol=symbol,
            source="benzinga",
            provider_created_at=updated_at - dt.timedelta(seconds=1),
            provider_updated_at=updated_at,
            received_at=OBSERVED,
        )
        for event_index, updated_at in enumerate(updates, start=1)
    )
    refs = [
        EvidenceRef(
            namespace="alpaca/news/coverage",
            record_id=assessment.assessment_id,
            observed_at=OBSERVED,
        )
    ]
    refs.extend(
        EvidenceRef(
            namespace="alpaca/news/article",
            record_id=item.observation_id,
            observed_at=item.received_at,
        )
        for item in observations
    )
    return AlpacaNewsOpportunityEvidenceSnapshot(
        manifest_id=manifest.manifest_id,
        assessment_id=assessment.assessment_id,
        universe_id=manifest.universe_id,
        symbol=symbol,
        observed_at=OBSERVED,
        observations=tuple(sorted(observations, key=lambda item: item.observation_id)),
        evidence_refs=tuple(sorted(refs, key=lambda item: item.canonical_id)),
        coverage=SourceCoverage(
            source_id="alpaca_news",
            observed_at=OBSERVED,
            record_count=len(observations),
            complete=True,
        ),
    )

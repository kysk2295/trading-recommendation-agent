from __future__ import annotations

import datetime as dt
from pathlib import Path

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
    UsNewsCatalystOpportunityProjection,
    project_registered_us_news_catalyst_opportunity,
)
from trading_agent.us_news_catalyst_research_registration import (
    UsNewsCatalystProjectionAuthorityRequest,
    register_us_news_catalyst_research_manifest,
    us_news_catalyst_strategy_version,
)

PROJECT = Path(__file__).resolve().parents[1]
REGISTRATION_MANIFEST = PROJECT / "examples" / "us_news_catalyst" / "research-registration.json"
CODE_VERSION = "us-news-catalyst-baseline-fixture-v1"
STRATEGY_VERSION = us_news_catalyst_strategy_version(CODE_VERSION)
SESSION_DATE = dt.date(2026, 7, 21)
OBSERVED = dt.datetime(2026, 7, 21, 14, tzinfo=dt.UTC)


def registered_ledger(tmp_path: Path) -> ExperimentLedgerStore:
    ledger = ExperimentLedgerStore(tmp_path / "experiment-ledger.sqlite3")
    _ = register_us_news_catalyst_research_manifest(REGISTRATION_MANIFEST, ledger)
    return ledger


def projected_evidence(
    ledger: ExperimentLedgerStore,
    *,
    treatment_symbols: tuple[str, ...] = ("AAPL", "MSFT"),
    zero_news_symbols: tuple[str, ...] = ("NVDA", "TSLA"),
) -> tuple[UsNewsCatalystOpportunityProjection, AlpacaNewsOpportunityEvidenceBundle]:
    symbols = tuple(sorted((*treatment_symbols, *zero_news_symbols)))
    request = AlpacaNewsRequest(
        collection_id="news-catalyst-trial-fixture",
        symbols=symbols,
        start_at=OBSERVED - dt.timedelta(hours=1),
        end_at=OBSERVED - dt.timedelta(seconds=1),
        limit=50,
        max_pages=2,
    )
    manifest = AlpacaNewsCoverageManifest(
        universe_id="news_catalyst_trial_fixture",
        cutoff_at=OBSERVED,
        requests=(request,),
    )
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
                article_count=len(treatment_symbols),
                latest_event_at=OBSERVED - dt.timedelta(seconds=10),
                failure_code=None,
            ),
        ),
        declared_symbol_count=len(symbols),
        successful_symbol_count=len(symbols),
        completeness_bps=10_000,
        accepted_article_count=len(treatment_symbols),
        latest_event_at=OBSERVED - dt.timedelta(seconds=10),
    )
    snapshots = tuple(
        _snapshot(
            manifest,
            assessment,
            symbol,
            index,
            has_news=symbol in treatment_symbols,
        )
        for index, symbol in enumerate(symbols, start=1)
    )
    bundle = AlpacaNewsOpportunityEvidenceBundle(
        manifest=manifest,
        assessment=assessment,
        snapshots=snapshots,
    )
    projection = project_registered_us_news_catalyst_opportunity(
        bundle,
        ledger,
        UsNewsCatalystProjectionAuthorityRequest(
            strategy_version=STRATEGY_VERSION,
            code_version=CODE_VERSION,
            projected_at=OBSERVED,
        ),
    )
    return projection, bundle


def _snapshot(
    manifest: AlpacaNewsCoverageManifest,
    assessment: AlpacaNewsCoverageAssessment,
    symbol: str,
    index: int,
    *,
    has_news: bool,
) -> AlpacaNewsOpportunityEvidenceSnapshot:
    observations = (
        (
            AlpacaNewsEvidenceObservation(
                event_id=f"{index:x}" * 64,
                receipt_id=f"{index + 8:x}" * 64,
                symbol=symbol,
                source="benzinga",
                provider_created_at=OBSERVED - dt.timedelta(seconds=11),
                provider_updated_at=OBSERVED - dt.timedelta(seconds=10),
                received_at=OBSERVED,
            ),
        )
        if has_news
        else ()
    )
    coverage = EvidenceRef(
        namespace="alpaca/news/coverage",
        record_id=assessment.assessment_id,
        observed_at=OBSERVED,
    )
    article_refs = tuple(
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
        observations=observations,
        evidence_refs=tuple(sorted((coverage, *article_refs), key=lambda item: item.canonical_id)),
        coverage=SourceCoverage(
            source_id="alpaca_news",
            observed_at=OBSERVED,
            record_count=len(observations),
            complete=True,
        ),
    )

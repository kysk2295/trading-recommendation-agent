from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
from pathlib import Path

import run_us_news_catalyst_opportunity as opportunity_cli
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
from trading_agent.alpaca_news_opportunity_evidence_artifact import (
    publish_alpaca_news_opportunity_evidence,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.signal_contract_models import EvidenceRef, SourceCoverage
from trading_agent.us_news_catalyst_opportunity_artifact import (
    load_us_news_catalyst_opportunity_projection,
)
from trading_agent.us_news_catalyst_research_registration import (
    register_us_news_catalyst_research_manifest,
)

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_us_news_catalyst_opportunity.py"
EXAMPLE = PROJECT / "examples" / "us_news_catalyst" / "research-registration.json"
REPORT_NAME = "us_news_catalyst_opportunity_ko.md"


def test_us_news_catalyst_opportunity_direct_help_is_self_contained() -> None:
    completed = subprocess.run(
        (str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "--evidence" in completed.stdout
    assert "--experiment-ledger" in completed.stdout


def test_us_news_catalyst_opportunity_ranked_fixture_replays_privately(
    tmp_path: Path,
) -> None:
    now = dt.datetime(2026, 7, 21, 13, 0, 30, tzinfo=dt.UTC)
    manifest, ledger = _registration(tmp_path)
    evidence, _ = publish_alpaca_news_opportunity_evidence(
        tmp_path / "evidence",
        _bundle(now - dt.timedelta(seconds=30), recent=True),
    )
    output = tmp_path / "output"
    arguments = _arguments(evidence, manifest, ledger.path, output)

    assert opportunity_cli.main(arguments, clock=lambda: now) == 0
    first_report = (output / REPORT_NAME).read_text(encoding="utf-8")
    assert opportunity_cli.main(arguments, clock=lambda: now) == 0
    replay_report = (output / REPORT_NAME).read_text(encoding="utf-8")

    artifacts = tuple(output.glob("us_news_catalyst_projection_*.json"))
    assert len(artifacts) == 1
    projection = load_us_news_catalyst_opportunity_projection(artifacts[0])
    assert projection.snapshot is not None
    assert projection.snapshot.candidates[0].symbol == "AAPL"
    assert "artifact 신규/재사용: 1/0" in first_report
    assert "artifact 신규/재사용: 0/1" in replay_report
    assert "order mutation: 0" in replay_report
    assert stat.S_IMODE(artifacts[0].stat().st_mode) == 0o600
    assert stat.S_IMODE((output / REPORT_NAME).stat().st_mode) == 0o600


def test_us_news_catalyst_opportunity_preserves_no_candidate_terminal(
    tmp_path: Path,
) -> None:
    now = dt.datetime(2026, 7, 21, 13, 0, 30, tzinfo=dt.UTC)
    manifest, ledger = _registration(tmp_path)
    evidence, _ = publish_alpaca_news_opportunity_evidence(
        tmp_path / "evidence",
        _bundle(now - dt.timedelta(seconds=30), recent=False),
    )
    output = tmp_path / "output"

    result = opportunity_cli.main(
        _arguments(evidence, manifest, ledger.path, output),
        clock=lambda: now,
    )

    artifacts = tuple(output.glob("us_news_catalyst_projection_*.json"))
    assert result == 2
    assert len(artifacts) == 1
    assert load_us_news_catalyst_opportunity_projection(artifacts[0]).snapshot is None
    assert "결과: no_candidates" in (output / REPORT_NAME).read_text(encoding="utf-8")


def test_us_news_catalyst_opportunity_rejects_stale_evidence(
    tmp_path: Path,
) -> None:
    now = dt.datetime(2026, 7, 21, 14, tzinfo=dt.UTC)
    manifest, ledger = _registration(tmp_path)
    evidence, _ = publish_alpaca_news_opportunity_evidence(
        tmp_path / "evidence",
        _bundle(now - dt.timedelta(minutes=6), recent=True),
    )
    output = tmp_path / "output"

    result = opportunity_cli.main(
        _arguments(evidence, manifest, ledger.path, output),
        clock=lambda: now,
    )

    assert result == 1
    assert not tuple(output.glob("us_news_catalyst_projection_*.json"))
    assert "결과: blocked" in (output / REPORT_NAME).read_text(encoding="utf-8")


def _registration(tmp_path: Path) -> tuple[Path, ExperimentLedgerStore]:
    manifest = tmp_path / "registration.json"
    payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    manifest.chmod(0o600)
    ledger = ExperimentLedgerStore(tmp_path / "experiment-ledger.sqlite3")
    _ = register_us_news_catalyst_research_manifest(manifest, ledger)
    return manifest, ledger


def _arguments(
    evidence: Path,
    manifest: Path,
    ledger: Path,
    output: Path,
) -> tuple[str, ...]:
    return (
        "--evidence",
        str(evidence),
        "--registration-manifest",
        str(manifest),
        "--experiment-ledger",
        str(ledger),
        "--output-dir",
        str(output),
    )


def _bundle(observed: dt.datetime, *, recent: bool) -> AlpacaNewsOpportunityEvidenceBundle:
    request = AlpacaNewsRequest(
        collection_id="news-catalyst-cli-fixture",
        symbols=("AAPL", "MSFT"),
        start_at=observed - dt.timedelta(hours=1),
        end_at=observed - dt.timedelta(seconds=1),
        limit=50,
        max_pages=2,
    )
    manifest = AlpacaNewsCoverageManifest(
        universe_id="news_catalyst_cli_fixture",
        cutoff_at=observed,
        requests=(request,),
    )
    updated = observed - dt.timedelta(seconds=10 if recent else 301)
    assessment = AlpacaNewsCoverageAssessment(
        manifest_id=manifest.manifest_id,
        universe_id=manifest.universe_id,
        assessed_at=observed,
        slices=(
            AlpacaNewsCoverageSlice(
                request_id=request.request_id,
                status=AlpacaNewsCoverageSliceStatus.SUCCESS,
                run_id="f" * 64,
                completed_at=observed,
                page_count=1,
                article_count=1,
                latest_event_at=updated,
                failure_code=None,
            ),
        ),
        declared_symbol_count=2,
        successful_symbol_count=2,
        completeness_bps=10_000,
        accepted_article_count=1,
        latest_event_at=updated,
    )
    observation = AlpacaNewsEvidenceObservation(
        event_id="a" * 64,
        receipt_id="b" * 64,
        symbol="AAPL",
        source="benzinga",
        provider_created_at=updated - dt.timedelta(seconds=1),
        provider_updated_at=updated,
        received_at=observed,
    )
    return AlpacaNewsOpportunityEvidenceBundle(
        manifest=manifest,
        assessment=assessment,
        snapshots=(
            _snapshot(manifest, assessment, "AAPL", (observation,)),
            _snapshot(manifest, assessment, "MSFT", ()),
        ),
    )


def _snapshot(
    manifest: AlpacaNewsCoverageManifest,
    assessment: AlpacaNewsCoverageAssessment,
    symbol: str,
    observations: tuple[AlpacaNewsEvidenceObservation, ...],
) -> AlpacaNewsOpportunityEvidenceSnapshot:
    coverage = EvidenceRef(
        namespace="alpaca/news/coverage",
        record_id=assessment.assessment_id,
        observed_at=assessment.assessed_at,
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
        observed_at=assessment.assessed_at,
        observations=observations,
        evidence_refs=tuple(sorted((coverage, *article_refs), key=lambda item: item.canonical_id)),
        coverage=SourceCoverage(
            source_id="alpaca_news",
            observed_at=assessment.assessed_at,
            record_count=len(observations),
            complete=True,
        ),
    )

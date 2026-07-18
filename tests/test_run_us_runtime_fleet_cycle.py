from __future__ import annotations

import datetime as dt
import stat
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest

import run_us_runtime_fleet_cycle as cli
from tests.alpaca_sip_runtime_fleet_fixtures import NEW_YORK, wire_bars
from tests.us_volume_profile_fixtures import volume_profile
from trading_agent.data_foundation_manifest import load_data_foundation_manifest
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)
from trading_agent.us_intraday_volume_profile_artifact import IntradayVolumeProfileArtifactStore
from trading_agent.us_market_data_fleet_audit_store import RuntimeFleetAuditStore
from trading_agent.us_opportunity_scanner_projection import UsOpportunityScannerProjector
from trading_agent.us_opportunity_scanner_store import UsOpportunityScannerStore

PROJECT = Path(__file__).resolve().parents[1]
FOUNDATION = PROJECT / "examples/data/us-orb-data-foundation-v1.json"
NOW = dt.datetime(2026, 7, 17, 10, 5, 30, tzinfo=NEW_YORK)
INSTRUMENT_ID = "us-eq-fixture-0001"


def test_help_is_available() -> None:
    with pytest.raises(SystemExit) as raised:
        _ = cli.parse_args(["--help"])

    assert raised.value.code == 0


def test_closed_session_blocks_before_secret_file_read(tmp_path: Path) -> None:
    report = tmp_path / "report"
    code = cli.main(
        _arguments(tmp_path, tmp_path / "missing.json", report),
        now=dt.datetime(2026, 7, 19, 10, 5, tzinfo=NEW_YORK),
    )

    assert code == 1
    assert "result: blocked" in _report(report)
    assert "account/order mutation: 0" in _report(report)


def test_ready_fixture_cycle_uses_only_alpaca_data_get(tmp_path: Path) -> None:
    scanner, profile = _inputs(tmp_path)
    secret = tmp_path / "alpaca.env"
    secret.write_text("APCA_API_KEY_ID=fixture\nAPCA_API_SECRET_KEY=fixture\n", encoding="utf-8")
    secret.chmod(0o600)
    requests: list[httpx2.Request] = []

    def client_factory() -> httpx2.Client:
        def respond(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            return httpx2.Response(
                200,
                json={"bars": {"FIXT": wire_bars("FIXT", 35)}, "next_page_token": None},
            )

        return httpx2.Client(
            base_url="https://data.alpaca.markets",
            transport=httpx2.MockTransport(respond),
            follow_redirects=False,
        )

    report = tmp_path / "report"
    code = cli.main(
        _arguments(tmp_path, profile, report, scanner=scanner, secret=secret),
        now=NOW,
        client_factory=client_factory,
    )

    assert code == 0
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.host == "data.alpaca.markets"
    assert requests[0].url.path == "/v2/stocks/bars"
    assert "result: ready" in _report(report)
    assert "gate: ready" in _report(report)
    audit = RuntimeFleetAuditStore(tmp_path / "audit.sqlite3").latest()
    assert audit is not None
    assert audit.gate_status == "ready"
    assert stat.S_IMODE((tmp_path / "audit.sqlite3").stat().st_mode) == 0o600


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    scanner = tmp_path / "scanner.sqlite3"
    observed_at = NOW - dt.timedelta(seconds=1)
    opportunity = OpportunitySnapshot(
        opportunity_id="us-opportunity-fix-20260717t140529z",
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.OPPORTUNITY_MANAGER,
            strategy_id="ranking_momentum",
        ),
        producer_strategy_version="fixture-v1",
        observed_at=observed_at,
        valid_until=NOW + dt.timedelta(minutes=1),
        candidates=(
            OpportunityCandidate(
                symbol="FIXT",
                rank=1,
                score=Decimal("12.5"),
                features=(FeatureValue(name="change_pct", value="12.5"),),
            ),
        ),
        evidence_refs=(EvidenceRef(namespace="fixture/ranking", record_id="fix:1", observed_at=observed_at),),
        source_coverage=(
            SourceCoverage(
                source_id="fixture_ranking",
                observed_at=observed_at,
                record_count=1,
                complete=True,
            ),
        ),
    )
    _ = UsOpportunityScannerProjector(
        UsOpportunityScannerStore(scanner),
        tmp_path / "scanner-canonical",
    ).project(opportunity, load_data_foundation_manifest(FOUNDATION))
    profile = IntradayVolumeProfileArtifactStore(tmp_path / "profile").append(
        volume_profile(INSTRUMENT_ID, NOW.date()),
    )
    return scanner, profile


def _arguments(
    tmp_path: Path,
    profile: Path,
    report: Path,
    *,
    scanner: Path | None = None,
    secret: Path | None = None,
) -> list[str]:
    return [
        "--scanner-store",
        str(tmp_path / "scanner.sqlite3" if scanner is None else scanner),
        "--profile",
        f"{INSTRUMENT_ID}={profile}",
        "--runtime-root",
        str(tmp_path / "runtime"),
        "--canonical-root",
        str(tmp_path / "canonical"),
        "--audit-store",
        str(tmp_path / "audit.sqlite3"),
        "--output-dir",
        str(report),
        "--secret-path",
        str(tmp_path / "missing.env" if secret is None else secret),
    ]


def _report(path: Path) -> str:
    return (path / cli.REPORT_NAME).read_text(encoding="utf-8")

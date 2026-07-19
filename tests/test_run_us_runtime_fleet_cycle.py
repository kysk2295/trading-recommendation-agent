from __future__ import annotations

import datetime as dt
import stat
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest

import run_us_runtime_fleet_cycle as cli
from tests.alpaca_sip_runtime_fleet_fixtures import NEW_YORK, wire_bars
from tests.test_us_quote_actionability import _conditional_publication
from tests.us_volume_profile_fixtures import volume_profile
from trading_agent.alpaca_sip_quote_actionability_manifest import (
    read_alpaca_sip_quote_actionability_manifest,
)
from trading_agent.contract_outbox import append_trade_signal_publication
from trading_agent.data_foundation_manifest import load_data_foundation_manifest
from trading_agent.research_evidence_artifact import load_research_evidence_artifact
from trading_agent.research_evidence_models import ClaimCorroborationStatus
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_intraday_volume_profile_artifact import IntradayVolumeProfileArtifactStore
from trading_agent.us_market_data_fleet_audit_store import RuntimeFleetAuditStore
from trading_agent.us_opportunity_scanner_projection import UsOpportunityScannerProjector
from trading_agent.us_opportunity_scanner_store import UsOpportunityScannerStore
from trading_agent.us_subscription_policy_state_store import SubscriptionPolicyStateStore

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
    assert not (tmp_path / "policy-state.sqlite3").exists()


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
        _arguments(
            tmp_path,
            profile,
            report,
            scanner=scanner,
            secret=secret,
            research_artifacts=True,
        ),
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
    assert "research evidence artifact: 1 new, 0 replay" in _report(report)
    artifacts = tuple((tmp_path / "research-artifacts").glob("research_evidence_*.json"))
    assert len(artifacts) == 1
    evidence = load_research_evidence_artifact(artifacts[0])
    assert len(evidence.claims) == 2
    assert all(item.corroboration_status is ClaimCorroborationStatus.UNCONFIRMED for item in evidence.claims)
    assert b"raw_receipt_ref" not in artifacts[0].read_bytes()
    assert stat.S_IMODE(artifacts[0].stat().st_mode) == 0o600
    audit = RuntimeFleetAuditStore(tmp_path / "audit.sqlite3").latest()
    assert audit is not None
    assert audit.gate_status == "ready"
    assert stat.S_IMODE((tmp_path / "audit.sqlite3").stat().st_mode) == 0o600
    policy_state = SubscriptionPolicyStateStore(tmp_path / "policy-state.sqlite3").latest()
    assert policy_state is not None
    assert policy_state.active[0].instrument_id == INSTRUMENT_ID
    assert policy_state.active[0].subscribed_at == NOW


def test_invalid_research_threshold_blocks_before_provider_or_secret(tmp_path: Path) -> None:
    report = tmp_path / "invalid-research-report"
    code = cli.main(
        [
            *_arguments(tmp_path, tmp_path / "profile.json", report, research_artifacts=True),
            "--minimum-rvol-bps",
            "0",
        ],
        now=NOW,
    )

    assert code == 1
    assert "result: blocked" in _report(report)
    assert not (tmp_path / "policy-state.sqlite3").exists()


def test_ready_cycle_dispatches_current_conditional_manifest(tmp_path: Path) -> None:
    scanner, profile = _inputs(tmp_path)
    secret = tmp_path / "alpaca.env"
    secret.write_text(
        "APCA_API_KEY_ID=fixture\nAPCA_API_SECRET_KEY=fixture\n",
        encoding="utf-8",
    )
    secret.chmod(0o600)
    signal_outbox = tmp_path / "trade-signals.v1.jsonl"
    assert (
        append_trade_signal_publication(
            signal_outbox,
            tmp_path / "cards",
            _fixture_conditional(),
        )
        is True
    )

    def client_factory() -> httpx2.Client:
        def respond(_request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                json={
                    "bars": {"FIXT": wire_bars("FIXT", 35)},
                    "next_page_token": None,
                },
            )

        return httpx2.Client(
            base_url="https://data.alpaca.markets",
            transport=httpx2.MockTransport(respond),
            follow_redirects=False,
        )

    report = tmp_path / "actionability-report"
    code = cli.main(
        _arguments(
            tmp_path,
            profile,
            report,
            scanner=scanner,
            secret=secret,
            actionability_manifests=True,
        ),
        now=NOW,
        client_factory=client_factory,
    )

    assert code == 0
    paths = tuple((tmp_path / "actionability-manifests").glob("*.json"))
    assert len(paths) == 1
    manifest = read_alpaca_sip_quote_actionability_manifest(paths[0])
    assert manifest.base_publication.signal.symbol == "FIXT"
    assert manifest.snapshot.instrument_id == INSTRUMENT_ID
    assert manifest.plan.bindings[0].symbol == "FIXT"
    assert "actionability manifests: 1 new, 0 replay" in _report(report)


def test_auto_profile_cycle_materializes_history_then_runs_current_get(tmp_path: Path) -> None:
    scanner, _profile = _inputs(tmp_path)
    secret = tmp_path / "alpaca.env"
    secret.write_text("APCA_API_KEY_ID=fixture\nAPCA_API_SECRET_KEY=fixture\n", encoding="utf-8")
    secret.chmod(0o600)
    requests: list[httpx2.Request] = []

    def client_factory() -> httpx2.Client:
        def respond(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            if request.url.params["asof"] == NOW.date().isoformat():
                return httpx2.Response(
                    200,
                    json={"bars": {"FIXT": wire_bars("FIXT", 35)}, "next_page_token": None},
                )
            return _historical_response(request)

        return httpx2.Client(
            base_url="https://data.alpaca.markets",
            transport=httpx2.MockTransport(respond),
            follow_redirects=False,
        )

    report = tmp_path / "auto-report"
    code = cli.main(
        _arguments(tmp_path, None, report, scanner=scanner, secret=secret, auto_profiles=True),
        now=NOW,
        client_factory=client_factory,
    )

    assert code == 0
    assert len(requests) == 21
    assert all(item.method == "GET" for item in requests)
    assert "result: ready" in _report(report)


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
    profile: Path | None,
    report: Path,
    *,
    scanner: Path | None = None,
    secret: Path | None = None,
    auto_profiles: bool = False,
    research_artifacts: bool = False,
    actionability_manifests: bool = False,
) -> list[str]:
    profile_arguments = (
        ["--auto-profile-root", str(tmp_path / "auto-profiles")]
        if auto_profiles
        else ["--profile", f"{INSTRUMENT_ID}={profile}"]
    )
    research_arguments = (
        ["--research-artifact-root", str(tmp_path / "research-artifacts")] if research_artifacts else []
    )
    actionability_arguments = (
        [
            "--conditional-signal-outbox",
            str(tmp_path / "trade-signals.v1.jsonl"),
            "--actionability-manifest-root",
            str(tmp_path / "actionability-manifests"),
        ]
        if actionability_manifests
        else []
    )
    return [
        "--scanner-store",
        str(tmp_path / "scanner.sqlite3" if scanner is None else scanner),
        *profile_arguments,
        "--runtime-root",
        str(tmp_path / "runtime"),
        "--canonical-root",
        str(tmp_path / "canonical"),
        "--audit-store",
        str(tmp_path / "audit.sqlite3"),
        "--policy-state-store",
        str(tmp_path / "policy-state.sqlite3"),
        "--output-dir",
        str(report),
        "--secret-path",
        str(tmp_path / "missing.env" if secret is None else secret),
        *research_arguments,
        *actionability_arguments,
    ]


def _fixture_conditional() -> TradeSignalPublication:
    publication = _conditional_publication(
        anchor=NOW,
        entry="100.10",
        stop="99.00",
        signal_id="fixture-current-signal",
    )
    payload = publication.model_dump(mode="json")
    payload["signal"]["symbol"] = "FIXT"
    return TradeSignalPublication.model_validate(payload)


def _report(path: Path) -> str:
    return (path / cli.REPORT_NAME).read_text(encoding="utf-8")


def _historical_response(request: httpx2.Request) -> httpx2.Response:
    opened = dt.datetime.fromisoformat(request.url.params["start"])
    closed = dt.datetime.fromisoformat(request.url.params["end"])
    count = int((closed - opened) / dt.timedelta(minutes=1)) + 1
    bars = tuple(
        {
            "t": (opened + dt.timedelta(minutes=index)).isoformat(),
            "o": 100.0,
            "h": 101.0,
            "l": 99.0,
            "c": 100.0,
            "v": 1000,
            "n": 10,
            "vw": 100.0,
        }
        for index in range(count)
    )
    return httpx2.Response(200, json={"bars": {"FIXT": bars}, "next_page_token": None})

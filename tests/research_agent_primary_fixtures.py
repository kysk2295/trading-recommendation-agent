from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal
from pathlib import Path

from trading_agent.contract_outbox import append_opportunity_snapshot
from trading_agent.market_context_models import MarketContextSnapshot, MarketRegimeLabel
from trading_agent.market_risk import MARKET_RISK_HEADER
from trading_agent.research_agent_service_config import (
    RESEARCH_AGENT_SERVICE_LABEL,
    ResearchAgentServiceConfig,
    write_research_agent_service_config,
)
from trading_agent.research_agent_sources import ResearchAgentSourcePaths
from trading_agent.research_agent_systematic import SystematicResearchActionConfig
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)
from trading_agent.store import PaperStore

NOW = dt.datetime(2026, 8, 3, 14, 35, tzinfo=dt.UTC)


def source_paths(tmp_path: Path) -> ResearchAgentSourcePaths:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    return ResearchAgentSourcePaths(
        outputs_root=outputs,
        market_context_root=outputs / "market-context",
        day_session_root=outputs / "live-sessions",
        swing_shadow_database=outputs / "swing" / "shadow.sqlite3",
        swing_review_database=outputs / "swing" / "review.sqlite3",
        experiment_ledger=outputs / "experiments" / "ledger.sqlite3",
        lane_review_database=outputs / "reviews" / "lane.sqlite3",
    )


def seed_opportunity(
    paths: ResearchAgentSourcePaths,
    *,
    observed_at: dt.datetime = NOW - dt.timedelta(minutes=1),
    valid_until: dt.datetime = NOW + dt.timedelta(minutes=2),
    spread: str | None = "12.5",
) -> None:
    features = (FeatureValue(name="change_pct", value="0.12"),)
    if spread is not None:
        features = (*features, FeatureValue(name="spread_bps", value=spread))
    snapshot = OpportunitySnapshot(
        opportunity_id=f"us-opportunity-{observed_at:%Y%m%dt%H%M%S}-abcd1234",
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.OPPORTUNITY_MANAGER,
            strategy_id="ranking_momentum",
        ),
        producer_strategy_version="ranking-momentum-v1",
        observed_at=observed_at,
        valid_until=valid_until,
        candidates=(
            OpportunityCandidate(
                symbol="ACME",
                rank=1,
                score=Decimal("0.12"),
                features=tuple(sorted(features, key=lambda item: item.name)),
            ),
        ),
        evidence_refs=(EvidenceRef(namespace="ranking", record_id="nas:1:acme", observed_at=observed_at),),
        source_coverage=(
            SourceCoverage(
                source_id="ranking_source",
                observed_at=observed_at,
                record_count=1,
                complete=True,
            ),
        ),
    )
    session = paths.day_session_root / observed_at.astimezone(dt.timezone(dt.timedelta(hours=-4))).strftime("%Y%m%d")
    session.mkdir(parents=True, exist_ok=True)
    assert append_opportunity_snapshot(session / "opportunities.v1.jsonl", snapshot)


def seed_market_context(
    paths: ResearchAgentSourcePaths,
    *,
    observed_at: dt.datetime = NOW - dt.timedelta(minutes=1),
    valid_until: dt.datetime = NOW + dt.timedelta(minutes=10),
    spread: str | None = "14.0",
) -> None:
    features = (FeatureValue(name="advance_decline", value="1.2"),)
    if spread is not None:
        features = (*features, FeatureValue(name="spread_bps", value=spread))
    snapshot = MarketContextSnapshot(
        context_id=f"us-context-{observed_at:%Y%m%dt%H%M%S}",
        market_id=MarketId.US_EQUITIES,
        observed_at=observed_at,
        valid_until=valid_until,
        regime_labels=(MarketRegimeLabel.TRENDING,),
        breadth_and_volatility_features=tuple(sorted(features, key=lambda item: item.name)),
        macro_and_flow_refs=("fred.vix",),
        coverage=(
            SourceCoverage(
                source_id="internal_breadth",
                observed_at=observed_at,
                record_count=500,
                complete=True,
            ),
        ),
        producer_version="market-context-v1",
    )
    paths.market_context_root.mkdir(parents=True, exist_ok=True)
    artifact = paths.market_context_root / "us-current.market-context.json"
    artifact.write_text(snapshot.model_dump_json(), encoding="utf-8")
    artifact.chmod(0o600)


def seed_day(
    paths: ResearchAgentSourcePaths,
    *,
    observed_at: dt.datetime = NOW - dt.timedelta(minutes=1),
    spread: str = "18.0",
) -> None:
    session = paths.day_session_root / observed_at.astimezone(dt.timezone(dt.timedelta(hours=-4))).strftime("%Y%m%d")
    session.mkdir(parents=True, exist_ok=True)
    store = PaperStore(session / "paper_recommendations.sqlite3")
    store.set_last_processed_bar("ACME", observed_at, 10.0)
    risk = session / "market_risk_screen.csv"
    with risk.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(MARKET_RISK_HEADER)
        writer.writerow(
            (
                observed_at.isoformat(),
                "NAS",
                "ACME",
                True,
                "",
                0.08,
                10.0,
                9.99,
                10.01,
                spread,
                58.0,
                2_000_000.0,
                300_000,
                1_000_000,
                0.3,
            )
        )
    risk.chmod(0o600)


def write_service_config(tmp_path: Path, paths: ResearchAgentSourcePaths) -> Path:
    executable = Path("/bin/echo")
    systematic = SystematicResearchActionConfig(
        project_root=tmp_path,
        uv_executable=executable,
        python_executable=executable,
        context=tmp_path / "systematic" / "context.json",
        response_fixture=None,
        hermes_executable=executable,
        model_id="fixture-service-v1",
        provider_id="fixture-provider",
        experiment_ledger=paths.experiment_ledger,
        receipt_root=tmp_path / "systematic" / "receipts",
        strategy_root=tmp_path / "systematic" / "strategies",
        manifest_root=tmp_path / "systematic" / "manifests",
        queue_root=tmp_path / "systematic" / "queue",
        input_activation=tmp_path / "systematic" / "input.json",
        artifact_root=tmp_path / "systematic" / "artifacts",
        review_root=tmp_path / "systematic" / "reviews",
        runs_root=tmp_path / "systematic" / "runs",
        max_runtime_seconds=120,
    )
    config = ResearchAgentServiceConfig(
        label=RESEARCH_AGENT_SERVICE_LABEL,
        project_root=tmp_path,
        uv_path=executable,
        hermes_executable=executable,
        model_id="fixture-service-v1",
        provider_id="fixture-provider",
        cycle_database=tmp_path / "state" / "cycles.sqlite3",
        output_root=tmp_path / "state" / "reports",
        hermes_database=tmp_path / "state" / "hermes.sqlite3",
        source_paths=paths,
        systematic=systematic,
    )
    path = (tmp_path / "private" / "service.json").absolute()
    assert write_research_agent_service_config(path, config)
    return path

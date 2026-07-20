from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.research_identity_models import AgentOperatingMode
from trading_agent.us_news_catalyst_research_registration import (
    US_NEWS_CATALYST_LANE,
    InvalidUsNewsCatalystResearchRegistrationError,
    UsNewsCatalystProjectionAuthorityRequest,
    register_us_news_catalyst_research_manifest,
    require_registered_us_news_catalyst_strategy,
    us_news_catalyst_strategy_version,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples" / "us_news_catalyst" / "research-registration.json"
CODE_VERSION = "us-news-catalyst-baseline-fixture-v1"


def test_registration_appends_exact_us_news_shadow_lineage_and_replays(
    tmp_path: Path,
) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment-ledger.sqlite3")

    first = register_us_news_catalyst_research_manifest(MANIFEST, ledger)
    second = register_us_news_catalyst_research_manifest(MANIFEST, ledger)

    assert first.hypotheses_created == 1
    assert first.versions_created == 1
    assert second.hypotheses_created == 0
    assert second.versions_created == 0
    stored = ledger.multi_market_strategy_versions()[0].registration
    assert stored.strategy_lane == US_NEWS_CATALYST_LANE
    assert stored.operating_mode is AgentOperatingMode.SHADOW
    assert stored.strategy_version == us_news_catalyst_strategy_version(CODE_VERSION)
    assert "no_order_authority" in stored.portfolio_policy


def test_projection_authority_rejects_unregistered_strategy(tmp_path: Path) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment-ledger.sqlite3")
    with ledger.writer():
        pass

    with pytest.raises(InvalidUsNewsCatalystResearchRegistrationError):
        require_registered_us_news_catalyst_strategy(
            ledger,
            UsNewsCatalystProjectionAuthorityRequest(
                strategy_version=us_news_catalyst_strategy_version(CODE_VERSION),
                code_version=CODE_VERSION,
                projected_at=dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC),
            ),
        )


def test_projection_authority_requires_exact_code_coupled_version(
    tmp_path: Path,
) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment-ledger.sqlite3")
    _ = register_us_news_catalyst_research_manifest(MANIFEST, ledger)

    with pytest.raises(InvalidUsNewsCatalystResearchRegistrationError):
        require_registered_us_news_catalyst_strategy(
            ledger,
            UsNewsCatalystProjectionAuthorityRequest(
                strategy_version=us_news_catalyst_strategy_version("different-runtime-code"),
                code_version="different-runtime-code",
                projected_at=dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC),
            ),
        )

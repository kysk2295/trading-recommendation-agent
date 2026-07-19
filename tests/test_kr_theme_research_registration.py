from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kr_theme_lane import (
    KR_THEME_LEADER_VWAP_RECLAIM_LANE,
    KR_THEME_OPPORTUNITY_LANE,
)
from trading_agent.kr_theme_research_registration import (
    InvalidKrThemeResearchRegistrationError,
    KrThemeProjectionAuthorityRequest,
    kr_theme_day_strategy_version,
    kr_theme_strategy_version,
    register_kr_theme_research_manifest,
    require_registered_kr_theme_day_strategy,
    require_registered_kr_theme_strategy,
)
from trading_agent.research_identity_models import AgentOperatingMode

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples" / "kr_theme_projection" / "research-registration.json"
DAY_MANIFEST = ROOT / "examples" / "kr_theme_projection" / "day-research-registration.json"


def test_registration_appends_exact_kr_shadow_lineage_and_replays(tmp_path: Path) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment-ledger.sqlite3")

    first = register_kr_theme_research_manifest(MANIFEST, ledger)
    second = register_kr_theme_research_manifest(MANIFEST, ledger)

    assert first.hypotheses_created == 1
    assert first.versions_created == 1
    assert second.hypotheses_created == 0
    assert second.versions_created == 0
    stored = ledger.multi_market_strategy_versions()[0].registration
    assert stored.strategy_lane == KR_THEME_OPPORTUNITY_LANE
    assert stored.operating_mode is AgentOperatingMode.SHADOW
    assert stored.strategy_version == kr_theme_strategy_version("kr-theme-fixture-code-v1")


def test_projection_verifier_rejects_unregistered_strategy(tmp_path: Path) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment-ledger.sqlite3")
    with ledger.writer():
        pass

    with pytest.raises(InvalidKrThemeResearchRegistrationError):
        require_registered_kr_theme_strategy(
            ledger,
            KrThemeProjectionAuthorityRequest(
                strategy_version=kr_theme_strategy_version("kr-theme-fixture-code-v1"),
                code_version="kr-theme-fixture-code-v1",
                projected_at=dt.datetime(2026, 7, 19, 9, tzinfo=dt.UTC),
            ),
        )


def test_day_registration_appends_exact_shadow_lane_and_replays(tmp_path: Path) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment-ledger.sqlite3")

    first = register_kr_theme_research_manifest(DAY_MANIFEST, ledger)
    second = register_kr_theme_research_manifest(DAY_MANIFEST, ledger)
    version = kr_theme_day_strategy_version("kr-theme-day-fixture-code-v1")
    stored = require_registered_kr_theme_day_strategy(
        ledger,
        KrThemeProjectionAuthorityRequest(
            strategy_version=version,
            code_version="kr-theme-day-fixture-code-v1",
            projected_at=dt.datetime(2026, 7, 19, 9, tzinfo=dt.UTC),
        ),
    )

    assert first.hypotheses_created == 1
    assert first.versions_created == 1
    assert second.hypotheses_created == 0
    assert second.versions_created == 0
    assert first.strategy_version == version
    assert stored.strategy_lane == KR_THEME_LEADER_VWAP_RECLAIM_LANE
    assert stored.operating_mode is AgentOperatingMode.SHADOW

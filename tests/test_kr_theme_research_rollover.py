from __future__ import annotations

import datetime as dt
import stat
from pathlib import Path

import pytest

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kr_same_cycle_opportunity_models import (
    load_kr_same_cycle_opportunity_policy,
)
from trading_agent.kr_theme_research_registration import (
    kr_theme_day_strategy_version,
    kr_theme_strategy_version,
    register_kr_theme_research_manifest,
)
from trading_agent.kr_theme_research_rollover import (
    InvalidKrThemeResearchRolloverError,
    prepare_kr_theme_research_rollover,
)

ROOT = Path(__file__).resolve().parents[1]
OPPORTUNITY_MANIFEST = (
    ROOT / "examples" / "kr_theme_projection" / "research-registration.json"
)
DAY_MANIFEST = (
    ROOT / "examples" / "kr_theme_projection" / "day-research-registration.json"
)
POLICY = (
    ROOT / "examples" / "kr_theme_projection" / "same-cycle-opportunity-policy.json"
)
CODE_VERSION = "a" * 40
RECORDED_AT = dt.datetime(2026, 7, 24, 7, tzinfo=dt.UTC)


def test_rollover_registers_two_current_versions_and_private_policy_atomically(
    tmp_path: Path,
) -> None:
    ledger = _base_ledger(tmp_path)
    output = tmp_path / "rollover"

    result = prepare_kr_theme_research_rollover(
        experiment_ledger=ledger,
        opportunity_manifest_path=OPPORTUNITY_MANIFEST,
        day_manifest_path=DAY_MANIFEST,
        policy_path=POLICY,
        output_dir=output,
        code_version=CODE_VERSION,
        recorded_at=RECORDED_AT,
    )
    replay = prepare_kr_theme_research_rollover(
        experiment_ledger=ledger,
        opportunity_manifest_path=OPPORTUNITY_MANIFEST,
        day_manifest_path=DAY_MANIFEST,
        policy_path=POLICY,
        output_dir=output,
        code_version=CODE_VERSION,
        recorded_at=RECORDED_AT + dt.timedelta(hours=1),
    )

    assert result.versions_created == 2
    assert replay.versions_created == 0
    assert replay.bundle_path == result.bundle_path
    assert replay.policy_path == result.policy_path
    assert result.opportunity_strategy_version == kr_theme_strategy_version(
        CODE_VERSION
    )
    assert result.day_strategy_version == kr_theme_day_strategy_version(CODE_VERSION)
    policy = load_kr_same_cycle_opportunity_policy(result.policy_path)
    assert policy.runtime_code_version == CODE_VERSION
    assert policy.producer_strategy_version == result.opportunity_strategy_version
    current = tuple(
        item.registration
        for item in ledger.multi_market_strategy_versions()
        if item.registration.code_version == CODE_VERSION
    )
    assert len(current) == 2
    assert {item.ledger_recorded_at for item in current} == {RECORDED_AT}
    for artifact in (result.bundle_path, result.policy_path):
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o600


def test_rollover_rejects_unregistered_templates_before_artifact_or_version(
    tmp_path: Path,
) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment-ledger.sqlite3")
    _ = register_kr_theme_research_manifest(OPPORTUNITY_MANIFEST, ledger)
    output = tmp_path / "rollover"

    with pytest.raises(InvalidKrThemeResearchRolloverError):
        _ = prepare_kr_theme_research_rollover(
            experiment_ledger=ledger,
            opportunity_manifest_path=OPPORTUNITY_MANIFEST,
            day_manifest_path=DAY_MANIFEST,
            policy_path=POLICY,
            output_dir=output,
            code_version=CODE_VERSION,
            recorded_at=RECORDED_AT,
        )

    assert len(ledger.multi_market_strategy_versions()) == 1
    assert not output.exists()


def _base_ledger(tmp_path: Path) -> ExperimentLedgerStore:
    ledger = ExperimentLedgerStore(tmp_path / "experiment-ledger.sqlite3")
    _ = register_kr_theme_research_manifest(OPPORTUNITY_MANIFEST, ledger)
    _ = register_kr_theme_research_manifest(DAY_MANIFEST, ledger)
    return ledger

from __future__ import annotations

import datetime as dt
import os
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.research_agent_primary_fixtures import write_service_config
from tests.research_agent_research_source_fixtures import NOW, populated_source_paths
from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1, ResearchAgentTriggerKind
from trading_agent.research_agent_research_source_inspection import (
    ResearchInspectionSourcePaths,
    _inspect_family,
    inspect_research_sources,
    load_research_inspection_source_paths,
)
from trading_agent.research_agent_source_adapters_research import ResearchSourcePaths, SwingSourceAdapter
from trading_agent.research_agent_source_common import ResearchAgentEvidenceMaterial
from trading_agent.research_agent_sources import ResearchAgentSourcePaths


class _NineEvidenceAdapter:
    def collect(
        self,
        paths: ResearchSourcePaths,
        now: dt.datetime,
    ) -> tuple[ResearchAgentEvidenceV1, ...]:
        return tuple(
            ResearchAgentEvidenceMaterial(
                family="swing_trading",
                trigger=ResearchAgentTriggerKind.NEW_DATA,
                source_key=(f"swing.sample.{index}" if index < 8 else "swing.blocked.sample"),
                observed_at=now,
                available_at=now,
                market_id="us_equities",
                canonical_payload=f"{index}",
            ).evidence()
            for index in range(9)
        )


def test_inspection_reports_exact_ready_research_families_with_bounded_provenance(tmp_path: Path) -> None:
    paths = populated_source_paths(tmp_path)

    swing = SwingSourceAdapter().collect(paths, NOW)

    inspection = inspect_research_sources(paths, NOW)

    assert all(item.available_at >= item.observed_at for item in swing)
    assert inspection.status == "ready"
    assert tuple(item.agent_family_id for item in inspection.families) == (
        "swing_trading",
        "systematic_quant",
        "derivatives_research",
    )
    assert all(item.status == "ready" and item.evidence_count >= 1 for item in inspection.families)
    assert all(len(item.source_keys) <= 8 and len(item.provenance_sha256) <= 8 for item in inspection.families)
    assert all(not item.truncated for item in inspection.families)
    assert (
        inspection.provider_calls
        == inspection.model_calls
        == inspection.heavy_processes
        == inspection.broker_mutation
        == 0
    )


def test_inspection_isolates_nonprivate_swing_ledger_from_ready_research_families(tmp_path: Path) -> None:
    paths = populated_source_paths(tmp_path)
    paths.swing_shadow_database.chmod(0o644)

    inspection = inspect_research_sources(paths, NOW)

    assert inspection.status == "invalid"
    assert [(item.agent_family_id, item.status) for item in inspection.families] == [
        ("swing_trading", "invalid"),
        ("systematic_quant", "ready"),
        ("derivatives_research", "ready"),
    ]


def test_private_service_config_loads_research_only_inspection_paths(tmp_path: Path) -> None:
    paths = populated_source_paths(tmp_path)
    config = write_service_config(tmp_path, paths)

    loaded = load_research_inspection_source_paths(config)
    inspection = inspect_research_sources(loaded, NOW)

    assert config.stat().st_mode & 0o777 == 0o600
    assert loaded.outputs_root == paths.outputs_root
    assert inspection.status == "ready"


def test_absent_research_inputs_remain_explicit_family_blockers(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    paths = ResearchAgentSourcePaths(
        outputs_root=outputs,
        market_context_root=outputs / "market-context",
        day_session_root=outputs / "live-sessions",
        swing_shadow_database=outputs / "swing" / "shadow.sqlite3",
        swing_review_database=outputs / "swing" / "review.sqlite3",
        experiment_ledger=outputs / "experiments" / "ledger.sqlite3",
        lane_review_database=outputs / "lane" / "review.sqlite3",
    )

    inspection = inspect_research_sources(paths, NOW)

    assert inspection.status == "blocked"
    assert [item.source_keys for item in inspection.families] == [
        ("swing.blocked.shadow_ledger_unavailable",),
        ("systematic.blocked.experiment_ledger_unavailable",),
        ("derivatives.blocked.options_entitlement_missing",),
    ]
    assert (
        inspection.provider_calls
        == inspection.model_calls
        == inspection.heavy_processes
        == inspection.broker_mutation
        == 0
    )


def test_missing_derivatives_entitlement_stays_blocked_without_estimate(tmp_path: Path) -> None:
    paths = populated_source_paths(tmp_path)
    derivatives = paths.outputs_root / "derivatives"
    (derivatives / "option-chain.sqlite3").unlink()
    (derivatives / "option-contracts.sqlite3").unlink()
    for authority in derivatives.glob("option_current_authority_*.json"):
        authority.unlink()

    inspection = inspect_research_sources(paths, NOW)

    derivative = inspection.families[2]
    assert derivative.status == "blocked"
    assert derivative.source_keys == ("derivatives.blocked.options_entitlement_missing",)
    assert derivative.evidence_count == 1


def test_wrong_schema_systematic_ledger_is_isolated_from_ready_families(tmp_path: Path) -> None:
    paths = populated_source_paths(tmp_path)
    paths.experiment_ledger.unlink()
    with sqlite3.connect(paths.experiment_ledger) as connection:
        _ = connection.execute("CREATE TABLE unrelated(value TEXT)")
    paths.experiment_ledger.chmod(0o600)

    inspection = inspect_research_sources(paths, NOW)

    assert [(item.agent_family_id, item.status) for item in inspection.families] == [
        ("swing_trading", "ready"),
        ("systematic_quant", "invalid"),
        ("derivatives_research", "ready"),
    ]


def test_hardlinked_swing_ledger_is_rejected_without_hiding_other_families(tmp_path: Path) -> None:
    paths = populated_source_paths(tmp_path)
    hardlink = paths.swing_shadow_database.with_name("shadow-hardlink.sqlite3")
    os.link(paths.swing_shadow_database, hardlink)
    linked_paths = ResearchAgentSourcePaths.model_validate(
        paths.model_dump(mode="python") | {"swing_shadow_database": hardlink}
    )

    inspection = inspect_research_sources(linked_paths, NOW)

    assert [(item.agent_family_id, item.status) for item in inspection.families] == [
        ("swing_trading", "invalid"),
        ("systematic_quant", "ready"),
        ("derivatives_research", "ready"),
    ]


def test_symlinked_research_source_boundary_is_rejected_before_inspection(tmp_path: Path) -> None:
    paths = populated_source_paths(tmp_path)
    alias = paths.swing_shadow_database.with_name("shadow-alias.sqlite3")
    alias.symlink_to(paths.swing_shadow_database)

    with pytest.raises(ValidationError, match="source_path_invalid"):
        ResearchInspectionSourcePaths.model_validate(
            {
                "outputs_root": paths.outputs_root,
                "swing_shadow_database": alias,
                "swing_review_database": paths.swing_review_database,
                "experiment_ledger": paths.experiment_ledger,
                "lane_review_database": paths.lane_review_database,
            }
        )


def test_inspection_reports_full_count_and_marks_truncated_samples(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    paths = ResearchInspectionSourcePaths(
        outputs_root=outputs,
        swing_shadow_database=outputs / "swing.sqlite3",
        swing_review_database=outputs / "review.sqlite3",
        experiment_ledger=outputs / "ledger.sqlite3",
        lane_review_database=outputs / "lane.sqlite3",
    )

    inspection = _inspect_family("swing_trading", _NineEvidenceAdapter(), paths, NOW)

    assert inspection.status == "blocked"
    assert inspection.evidence_count == 9
    assert inspection.truncated is True
    assert len(inspection.source_keys) == len(inspection.provenance_sha256) == 8

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from tests.test_contract_outbox import OBSERVED_AT, _opportunity, _publication
from trading_agent.contract_outbox import append_opportunity_snapshot, append_trade_signal_publication
from trading_agent.dashboard_reviewer_lifecycle import PersistedChampionAuthority
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.hermes_delivery_projection import HermesProjectionSources, project_contract_outboxes
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.hermes_query_service import HermesAgentQueryService, HermesQueryAgentFamily
from trading_agent.lane_review_store import InvalidLaneReviewSourceError


class _AllocationAuthority:
    def __init__(self, champions: tuple[PersistedChampionAuthority, ...]) -> None:
        self._champions = champions

    def allocation_manager_is_available(self) -> bool:
        return len({(champion.family_id, champion.lane_id) for champion in self._champions}) >= 2

    def champions(self) -> tuple[PersistedChampionAuthority, ...]:
        return self._champions


class _InvalidAllocationAuthority:
    def champions(self) -> tuple[PersistedChampionAuthority, ...]:
        raise InvalidLaneReviewSourceError


def test_query_returns_separate_agent_opinions_without_blended_verdict(tmp_path: Path) -> None:
    # Given
    store = _projected_store(tmp_path)

    # When
    result = HermesAgentQueryService(store).query("ACME", observed_at=OBSERVED_AT + dt.timedelta(seconds=10))

    # Then
    assert [item.agent_family for item in result.opinions] == list(HermesQueryAgentFamily)
    assert result.opinions[0].status == "watch"
    assert result.opinions[2].status == "conditional"
    assert all(item.status == "blocked_missing_evidence" for item in result.opinions[1:2] + result.opinions[3:])
    assert result.blended_verdict is None
    assert result.allocation_manager.state == "disabled"
    assert result.allocation_manager.reason == "authority_unavailable"
    assert result.allocation_manager.independent_champion_count == 0
    assert not result.allocation_manager.direct_order_authority
    assert not result.allocation_manager.symbol_selection_authority


def test_query_blocks_unknown_symbol_and_stale_projection(tmp_path: Path) -> None:
    # Given
    service = HermesAgentQueryService(_projected_store(tmp_path))

    # When
    unknown = service.query("NONE", observed_at=OBSERVED_AT + dt.timedelta(seconds=10))
    stale = service.query("ACME", observed_at=OBSERVED_AT + dt.timedelta(days=2))

    # Then
    assert all(item.status == "blocked_missing_evidence" for item in unknown.opinions)
    assert stale.opinions[0].status == "blocked_stale_projection"
    assert stale.opinions[2].status == "blocked_stale_projection"


def test_query_exposes_available_allocation_without_granting_direct_authority(tmp_path: Path) -> None:
    # Given
    champions = (
        PersistedChampionAuthority(
            strategy_version="day-v1",
            family_id="day_trading",
            lane_id="intraday_momentum",
            lifecycle_ref="a" * 64,
            reviewer_ref="b" * 64,
            candidate_refs=("c" * 64,),
        ),
        PersistedChampionAuthority(
            strategy_version="swing-v1",
            family_id="swing_trading",
            lane_id="swing_momentum",
            lifecycle_ref="d" * 64,
            reviewer_ref="e" * 64,
            candidate_refs=("f" * 64,),
        ),
    )
    service = HermesAgentQueryService(
        _projected_store(tmp_path),
        allocation_authority=_AllocationAuthority(champions),
    )

    # When
    result = service.query("ACME", observed_at=OBSERVED_AT + dt.timedelta(seconds=10))

    # Then
    assert result.allocation_manager.state == "available"
    assert result.allocation_manager.reason == "two_independent_champions_present"
    assert result.allocation_manager.independent_champion_count == 2
    assert result.allocation_manager.evidence_refs == ("a" * 64, "b" * 64, "d" * 64, "e" * 64)
    assert not result.allocation_manager.direct_order_authority
    assert not result.allocation_manager.symbol_selection_authority


def test_query_fails_closed_when_allocation_authority_is_corrupt(tmp_path: Path) -> None:
    # Given
    service = HermesAgentQueryService(
        _projected_store(tmp_path),
        allocation_authority=_InvalidAllocationAuthority(),
    )

    # When
    result = service.query("ACME", observed_at=OBSERVED_AT + dt.timedelta(seconds=10))

    # Then
    assert result.allocation_manager.state == "disabled"
    assert result.allocation_manager.reason == "authority_unavailable"
    assert result.allocation_manager.independent_champion_count == 0
    assert result.allocation_manager.evidence_refs == ()


def test_cli_query_happy_path_and_malformed_project_fail_closed(tmp_path: Path, capsys) -> None:
    # Given
    from run_hermes_delivery import main

    store = _projected_store(tmp_path)
    experiment_ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    with experiment_ledger.writer():
        pass
    lane_review = tmp_path / "missing-lane-review.sqlite3"
    (tmp_path / "malformed.jsonl").write_text("{not-json}\n", encoding="utf-8")

    # When
    success = main(
        (
            "query",
            "--database",
            str(store.path),
            "--symbol",
            "ACME",
            "--observed-at",
            (OBSERVED_AT + dt.timedelta(seconds=10)).isoformat(),
            "--experiment-ledger",
            str(experiment_ledger.path),
            "--lane-review",
            str(lane_review),
        )
    )
    payload = json.loads(capsys.readouterr().out)
    blocked = main(
        (
            "project",
            "--database",
            str(tmp_path / "bad.sqlite3"),
            "--opportunities",
            str(tmp_path / "malformed.jsonl"),
            "--signals",
            str(tmp_path / "missing.jsonl"),
        )
    )
    blocked_payload = json.loads(capsys.readouterr().out)

    # Then
    assert success == 0
    assert payload["result"] == "queried"
    assert payload["opinion_count"] == len(HermesQueryAgentFamily)
    assert payload["allocation_manager"]["state"] == "disabled"
    assert payload["allocation_manager"]["reason"] == "two_independent_champions_required"
    assert payload["allocation_manager"]["independent_champion_count"] == 0
    assert blocked == 2
    assert blocked_payload == {"reason": "invalid_projection_source", "result": "blocked"}


def _projected_store(tmp_path: Path) -> HermesDeliveryStore:
    sources = HermesProjectionSources(
        opportunity_outbox=tmp_path / "opportunities.v1.jsonl",
        signal_outbox=tmp_path / "trade-signals.v1.jsonl",
    )
    _ = append_opportunity_snapshot(sources.opportunity_outbox, _opportunity())
    _ = append_trade_signal_publication(sources.signal_outbox, tmp_path / "cards", _publication(signal_id="signal-1"))
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    with store.writer() as writer:
        _ = project_contract_outboxes(sources, writer)
    return store

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from tests.test_contract_outbox import OBSERVED_AT, _opportunity, _publication
from trading_agent.contract_outbox import append_opportunity_snapshot, append_trade_signal_publication
from trading_agent.hermes_delivery_projection import HermesProjectionSources, project_contract_outboxes
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.hermes_query_service import HermesAgentQueryService, HermesQueryAgentFamily


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


def test_cli_query_happy_path_and_malformed_project_fail_closed(tmp_path: Path, capsys) -> None:
    # Given
    from run_hermes_delivery import main

    store = _projected_store(tmp_path)
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

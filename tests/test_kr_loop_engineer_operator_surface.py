from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from tests.test_kr_autonomous_outcome_learning import _paths
from tests.test_kr_loop_engineer_cli import _bundle, _memory_record
from tests.test_kr_loop_engineer_controller import _shadow, _SuccessfulMutation, _validation
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.dashboard_kr_autonomous_operator import project_kr_autonomous_operator
from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_autonomous_hermes import project_kr_autonomous_state
from trading_agent.kr_loop_engineer_controller import KrLoopEngineerController
from trading_agent.kr_loop_engineer_models import KrLoopHealthReceipt
from trading_agent.kr_loop_engineer_store import KrLoopEngineerStore
from trading_agent.kr_loop_engineer_sync import sync_kr_loop_bundles

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 8, 27, 18, 0, tzinfo=KST)
BASE = "a" * 40


def test_loop_lifecycle_projects_redacted_dashboard_lineage_and_hermes_events(tmp_path: Path) -> None:
    # Given: a code challenger that was promoted after two future sessions and then rolled back.
    paths = _paths(tmp_path)
    final = _rolled_back_lifecycle(paths)

    # When: dashboard and Hermes project the durable control-plane history.
    dashboard = project_kr_autonomous_operator(paths, now=NOW + dt.timedelta(days=3))
    hermes_database = tmp_path / "hermes.sqlite3"
    with HermesDeliveryStore(hermes_database).writer() as writer:
        projected = project_kr_autonomous_state(paths, writer, projected_source_ids=frozenset())
    events = HermesDeliveryReader(hermes_database).events()

    # Then: every phase is visible without paths, raw patches, or profitability claims.
    loop_items = tuple(item for item in dashboard.research.items if "Loop Engineer" in item.label)
    assert loop_items
    assert any("rolled_back" in (item.value or "") for item in loop_items)
    assert any(edge.kind == "derived_from" for edge in dashboard.edges)
    loop_events = tuple(item for item in events if item.status.startswith("loop_"))
    assert projected.inserted >= len(loop_events) >= 6
    assert loop_events[-1].source_event_id == final.snapshot_id
    assert "복귀" in loop_events[-1].rendered_text
    outbound = "\n".join(item.rendered_text for item in loop_events)
    assert str(tmp_path) not in outbound
    assert "diff" not in outbound.lower()
    assert "수익" not in outbound
    assert "실거래 권한=false" in outbound


def _rolled_back_lifecycle(paths):
    bundle = _bundle()
    with AutonomousMemoryStore(paths.memory_database).writer() as writer:
        assert writer.append(_memory_record(bundle))
    assert sync_kr_loop_bundles(paths, base_commit=BASE, now=NOW).inserted == 1
    store = KrLoopEngineerStore(paths.loop_database)
    controller = KrLoopEngineerController(store, _SuccessfulMutation())
    ready = controller.mutate(bundle, now=NOW + dt.timedelta(minutes=1))
    shadowing = controller.validate(ready.candidate_id, _validation(ready))
    _ = controller.record_shadow(shadowing.candidate_id, _shadow(1))
    _ = controller.record_shadow(shadowing.candidate_id, _shadow(2))
    release = store.releases()[-1]
    return controller.record_health(
        KrLoopHealthReceipt(
            release_id=release.release_id,
            observed_at=NOW + dt.timedelta(days=3),
            error_rate=Decimal("0.00"),
            data_eligibility_failures=1,
            order_mismatches=0,
            research_task_losses=0,
            evidence_refs=("health:data-eligibility",),
        )
    )

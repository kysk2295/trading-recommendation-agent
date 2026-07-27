from __future__ import annotations

import datetime as dt
import hashlib
import stat
from pathlib import Path

from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1
from trading_agent.dashboard_kr_autonomous_bridge import (
    publish_kr_autonomous_triggers,
)
from trading_agent.dashboard_kr_market_runtime import project_kr_realtime_cycle
from trading_agent.dashboard_trigger_authority import TriggerAuthorityStore
from trading_agent.kr_source_collection_models import (
    KrSourceCollectionRun,
    KrSourceReceipt,
)
from trading_agent.kr_source_cycle import finalize_kr_source_cycle
from trading_agent.kr_theme_models import (
    KrCatalystSource,
    KrCoverageStatus,
)
from trading_agent.kr_theme_store import KrThemeStore
from trading_agent.private_query_file import read_private_text_query_only

UTC = dt.UTC
NOW = dt.datetime(2026, 7, 27, 5, 21, tzinfo=UTC)
CYCLE_ID = "kr-m3-live-20260727-test-001"


def test_completed_kr_source_cycle_publishes_one_authorized_opportunity_trigger(
    tmp_path: Path,
) -> None:
    outputs = tmp_path / "outputs"
    database = (
        outputs
        / "kr_theme"
        / "m3_live"
        / "2026-07-27-watch-test"
        / "kr_theme.sqlite3"
    )
    _seed_complete_cycle(database)
    state_root = tmp_path / "autonomous-state"

    first = publish_kr_autonomous_triggers(
        outputs,
        state_root=state_root,
        pinned_code_sha="a" * 40,
        now=NOW,
    )
    replay = publish_kr_autonomous_triggers(
        outputs,
        state_root=state_root,
        pinned_code_sha="a" * 40,
        now=NOW,
    )

    assert len(first) == 1
    assert replay == ()
    assert stat.S_IMODE(first[0].stat().st_mode) == 0o600
    trigger = AutonomousTriggerV1.model_validate_json(
        read_private_text_query_only(first[0])
    )
    assert trigger.agent_family_id == "opportunity_manager"
    assert trigger.trigger_type == "new_data"
    assert trigger.source_receipt_ids
    assert trigger.dedupe_key == f"kr-new-data-{CYCLE_ID}"
    evidence = (
        state_root
        / "authorities"
        / "evidence"
        / f"{trigger.trigger_id}.json"
    )
    assert hashlib.sha256(
        read_private_text_query_only(evidence).encode()
    ).hexdigest() == trigger.payload_sha256
    authorities = TriggerAuthorityStore(state_root / "authorities").records()
    assert len(authorities) == 1
    assert authorities[0].authority_id == trigger.trigger_id


def test_completed_kr_source_cycle_projects_current_detection_counts(
    tmp_path: Path,
) -> None:
    outputs = tmp_path / "outputs"
    database = (
        outputs
        / "kr_theme"
        / "m3_live"
        / "2026-07-27-watch-test"
        / "kr_theme.sqlite3"
    )
    _seed_complete_cycle(database)

    item, nodes, edges = project_kr_realtime_cycle(outputs, now=NOW)

    assert item.state == "populated"
    assert item.value == f"records=0;coverage=4/4;cycle={CYCLE_ID}"
    assert nodes[0].kind == "source_receipt"
    assert edges == ()


def _seed_complete_cycle(database: Path) -> None:
    started = NOW - dt.timedelta(minutes=2)
    payload = b'{"rt_cd":"0","output":[]}'
    receipt = KrSourceReceipt(
        source_run_id=f"{CYCLE_ID}:dart",
        source=KrCatalystSource.DART,
        request_key="opendart:list:20260727:page:1",
        received_at=started + dt.timedelta(seconds=1),
        http_status=200,
        content_type="application/json",
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )
    store = KrThemeStore(database)
    with store.writer() as writer:
        _ = writer.append_source_receipt(receipt, payload)
        for offset, source in enumerate(KrCatalystSource):
            source_receipts = (
                (receipt.receipt_id,)
                if source is KrCatalystSource.DART
                else ()
            )
            run = KrSourceCollectionRun(
                source_run_id=f"{CYCLE_ID}:{source.value}",
                collection_cycle_id=CYCLE_ID,
                source=source,
                adapter_version=f"{source.value}-fixture-v1",
                started_at=started + dt.timedelta(seconds=offset),
                completed_at=started + dt.timedelta(minutes=1, seconds=offset),
                status=KrCoverageStatus.SUCCESS,
                record_count=0,
                receipt_ids=source_receipts,
            )
            _ = writer.append_source_run(run)
    result = finalize_kr_source_cycle(store, collection_cycle_id=CYCLE_ID)
    assert result.cycle is not None
    assert result.cycle.complete

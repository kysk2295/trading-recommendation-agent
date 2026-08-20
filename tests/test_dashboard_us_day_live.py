from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

import pytest

from tests.dashboard_paper_projection_fixtures import (
    FINGERPRINT,
    append_daily_snapshot,
    finalized_snapshot,
    safety_plan,
)
from tests.day_agent_support import day_task
from tests.test_day_learning_report_models import _payload, _report
from tests.test_us_day_signal_admission import _eligible_request
from trading_agent.alpaca_trade_updates import parse_alpaca_trade_update
from trading_agent.dashboard_paper_finalized_terminal_writer import publish_finalized_paper_terminal
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.dashboard_us_day_live import DayAgentVersionView
from trading_agent.dashboard_us_day_paper import FinalizedPaperProjectionBundle, read_finalized_paper_bundle
from trading_agent.day_agent_task_store import DayAgentTaskStore
from trading_agent.day_learning_report_models import MarketCloseReport, MarketCloseReportPayload
from trading_agent.day_learning_report_store import publish_market_close_report
from trading_agent.execution_store import ExecutionStore
from trading_agent.hermes_delivery_models import HermesDeliveryKind, build_hermes_delivery_event
from trading_agent.hermes_delivery_projection import HermesProjectionRecord, project_outcomes
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.paper_execution_models import (
    BrokerOrderEventType,
    BrokerOrderId,
    IntentId,
    PaperOrderIntent,
    PaperOrderSide,
)
from trading_agent.paper_mutation_keys import paper_mutation_key
from trading_agent.paper_mutation_ledger_models import (
    PaperMutationEvent,
    PaperMutationEventType,
    PaperMutationIntent,
    PaperMutationOperation,
)
from trading_agent.paper_protective_oco_models import ProtectiveOcoClientOrderId, ProtectiveOcoExitPlan
from trading_agent.paper_protective_oco_store import protective_oco_plan_key
from trading_agent.paper_safety_models import PaperSafetyPhase
from trading_agent.paper_stream_recovery_models import PaperStreamRecoveryObservation
from trading_agent.research_identity_models import MarketId
from trading_agent.us_day_thesis_models import DayTradeDecision, ThesisChangeKind, UsDayThesisChange, UsDayTradeThesis
from trading_agent.us_day_thesis_store import UsDayThesisStore

NOW = dt.datetime(2026, 8, 20, 14, 7, tzinfo=dt.UTC)


@dataclass(frozen=True, slots=True)
class _VersionReader:
    records: tuple[DayAgentVersionView, ...]

    def versions(self) -> tuple[DayAgentVersionView, ...]:
        return self.records


@dataclass(frozen=True, slots=True)
class _RaisingVersionReader:
    def versions(self) -> tuple[DayAgentVersionView, ...]:
        raise RuntimeError("untrusted reader failed")


def test_dashboard_projects_canonical_thesis_task_version_and_close_report(tmp_path: Path) -> None:
    outputs = _day_outputs(tmp_path)
    recommendation = _eligible_request().thesis
    no_trade = _no_trade(recommendation.observed_at)
    store = UsDayThesisStore(outputs / "us_day" / "theses")
    assert store.publish_thesis(recommendation)
    assert store.publish_thesis(no_trade)
    _append_change(store, recommendation)
    reader = _seed_versioned_task(
        outputs,
        recommendation.agent_version_id,
        shadows=(
            DayAgentVersionView(
                version_id="c" * 64,
                deployment_state="shadow",
                task_id="task-20260820-NVDA",
                observed_at=NOW - dt.timedelta(minutes=1),
            ),
        ),
    )
    _publish_close_report(outputs)

    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW, day_version_reader=reader)

    markets = {item.item_id: item.value for item in snapshot.workspaces.markets.items}
    paper = {item.item_id: item.value for item in snapshot.workspaces.paper.items}
    assert markets["day.regime"] == "risk_on"
    assert markets["day.theme.1"] == "semiconductor_infrastructure · leading"
    assert markets["day.leader.1"] == "NVDA · leader"
    assert markets["day.recommendation.NVDA"] == "entry 200.05 · stop 199.5 · targets 200.60/201.15"
    assert markets["day.thesis_change.NVDA"] == "hold"
    assert markets["day.no_trade.1"] == "NO_TRADE · setup_not_confirmed"
    assert markets["day.champion"] == recommendation.agent_version_id[:12]
    assert markets["day.shadow.1"] == "c" * 12
    assert paper["day.close_review.1"] == "finalized"
    assert "day.paper.NVDA" not in paper
    assert "day-agent-live-reader-v1" in snapshot.projection.reader_versions
    assert any(edge.kind == "executed_as" for edge in snapshot.traces.edges)


def test_dashboard_sorts_challengers_and_terminal_records_before_cap(tmp_path: Path) -> None:
    outputs = _day_outputs(tmp_path)
    store = UsDayThesisStore(outputs / "us_day" / "theses")
    for index in reversed(range(30)):
        assert store.publish_thesis(_no_trade(NOW - dt.timedelta(minutes=index), index=index))
    reader = _seed_versioned_task(
        outputs,
        "a" * 64,
        shadows=(
            DayAgentVersionView(
                version_id="d" * 64,
                deployment_state="shadow",
                task_id="task-20260820-NVDA",
                observed_at=NOW - dt.timedelta(minutes=2),
            ),
            DayAgentVersionView(
                version_id="c" * 64,
                deployment_state="shadow",
                task_id="task-20260820-NVDA",
                observed_at=NOW - dt.timedelta(minutes=1),
            ),
        ),
    )

    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW, day_version_reader=reader)

    values = {item.item_id: item.value for item in snapshot.workspaces.markets.items}
    assert values["day.shadow.1"] == "c" * 12
    assert values["day.shadow.2"] == "d" * 12
    assert values["day.no_trade.1"] == "NO_TRADE · setup_not_confirmed"
    assert len(snapshot.workspaces.markets.items) <= 24
    assert snapshot.workspaces.markets.truncated is True


def test_dashboard_blocks_only_day_items_for_stale_or_corrupt_source(tmp_path: Path) -> None:
    outputs = _day_outputs(tmp_path)
    stale = _no_trade(NOW - dt.timedelta(hours=2))
    store = UsDayThesisStore(outputs / "us_day" / "theses")
    assert store.publish_thesis(stale)

    stale_snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)
    stale_item = next(item for item in stale_snapshot.workspaces.markets.items if item.item_id == "day.source")
    assert stale_item.state == "blocked"

    artifact = outputs / "us_day" / "theses" / "theses" / f"{stale.thesis_id}.json"
    artifact.chmod(0o644)
    corrupt_snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)
    corrupt_item = next(item for item in corrupt_snapshot.workspaces.markets.items if item.item_id == "day.source")
    assert corrupt_item.state == "corrupt"
    assert corrupt_snapshot.workspaces.paper.state != "corrupt"


def test_dashboard_isolates_untrusted_day_version_reader_failures(tmp_path: Path) -> None:
    outputs = _day_outputs(tmp_path)
    recommendation = _eligible_request().thesis
    store = UsDayThesisStore(outputs / "us_day" / "theses")
    assert store.publish_thesis(recommendation)

    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW, day_version_reader=_RaisingVersionReader())

    markets = {item.item_id: item for item in snapshot.workspaces.markets.items}
    assert markets["day.version_source"].state == "blocked"
    assert "day.recommendation.NVDA" in markets
    assert snapshot.workspaces.overview.state != "corrupt"
    assert snapshot.workspaces.paper.state != "corrupt"


def test_dashboard_blocks_only_invalid_day_version_records(tmp_path: Path) -> None:
    outputs = _day_outputs(tmp_path)
    recommendation = _eligible_request().thesis
    store = UsDayThesisStore(outputs / "us_day" / "theses")
    assert store.publish_thesis(recommendation)
    malformed = DayAgentVersionView.model_construct(
        version_id="not-a-version",
        deployment_state="champion",
        task_id="missing-task",
        observed_at=dt.datetime(2026, 8, 20, 14, 8),
    )

    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW, day_version_reader=_VersionReader((malformed,)))

    markets = {item.item_id: item for item in snapshot.workspaces.markets.items}
    assert markets["day.version_source"].state == "blocked"
    assert "day.champion" not in markets
    assert "day.recommendation.NVDA" in markets


def test_dashboard_projects_lifecycle_only_from_finalized_paper_ledger(tmp_path: Path) -> None:
    outputs = _day_outputs(tmp_path)
    recommendation = _eligible_request().thesis
    store = UsDayThesisStore(outputs / "us_day" / "theses")
    assert store.publish_thesis(recommendation)
    _append_change(store, recommendation, ThesisChangeKind.CLOSE, "filled-and-closed-in-note")
    _append_finalized_canceled_intent(outputs, recommendation)
    _append_unrelated_hermes_exit(outputs, recommendation.thesis_id)

    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    paper = {item.item_id: item.value for item in snapshot.workspaces.paper.items}
    assert paper["day.paper.NVDA"] == "submitted · reconciled"
    assert "day.paper_exit.NVDA" not in paper


def test_dashboard_sorts_immutable_close_reviews(tmp_path: Path) -> None:
    outputs = _day_outputs(tmp_path)
    _publish_close_reports_in_source_order(outputs)

    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    reviews = tuple(item for item in snapshot.workspaces.paper.items if item.item_id.startswith("day.close_review."))
    assert tuple(item.item_id for item in reviews) == ("day.close_review.1", "day.close_review.2")
    assert reviews[0].observed_at == NOW
    assert reviews[1].observed_at == NOW - dt.timedelta(days=1)


@pytest.mark.parametrize(
    "observed_at",
    (dt.datetime(2026, 8, 20, 14, 7), NOW + dt.timedelta(seconds=1)),
)
def test_dashboard_blocks_naive_or_future_day_version_records(tmp_path: Path, observed_at: dt.datetime) -> None:
    outputs = _day_outputs(tmp_path)
    recommendation = _eligible_request().thesis
    store = UsDayThesisStore(outputs / "us_day" / "theses")
    assert store.publish_thesis(recommendation)
    malformed = DayAgentVersionView.model_construct(
        version_id="a" * 64,
        deployment_state="champion",
        task_id="task-20260820-NVDA",
        observed_at=observed_at,
    )

    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW, day_version_reader=_VersionReader((malformed,)))

    markets = {item.item_id: item for item in snapshot.workspaces.markets.items}
    assert markets["day.version_source"].state == "blocked"
    assert "day.recommendation.NVDA" in markets


def test_dashboard_redacts_projected_task_hypothesis(tmp_path: Path) -> None:
    outputs = _day_outputs(tmp_path)
    recommendation = _eligible_request().thesis
    store = UsDayThesisStore(outputs / "us_day" / "theses")
    assert store.publish_thesis(recommendation)
    reader = _seed_versioned_task(
        outputs,
        recommendation.agent_version_id,
        hypothesis="Bearer api-key-should-not-project",
    )

    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW, day_version_reader=reader)

    values = {item.item_id: item.value for item in snapshot.workspaces.markets.items}
    assert "api-key-should-not-project" not in (values["day.regime"] or "")


def test_verified_paper_ledger_rejects_identity_change_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outputs = _day_outputs(tmp_path)
    recommendation = _eligible_request().thesis
    store = UsDayThesisStore(outputs / "us_day" / "theses")
    assert store.publish_thesis(recommendation)
    _append_finalized_canceled_intent(outputs, recommendation)
    original = ExecutionStore.reconciliation_ledger

    def replace_ledger(execution: ExecutionStore):
        with execution.writer() as writer:
            assert writer.save_intent(_replacement_intent(), quantity=1)
        return original(execution)

    monkeypatch.setattr(ExecutionStore, "reconciliation_ledger", replace_ledger)

    assert not isinstance(read_finalized_paper_bundle(outputs, now=NOW), FinalizedPaperProjectionBundle)


def test_dashboard_reads_one_finalized_bundle_when_ledger_view_changes_between_consumers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = _day_outputs(tmp_path)
    recommendation = _eligible_request().thesis
    assert UsDayThesisStore(outputs / "us_day" / "theses").publish_thesis(recommendation)
    _append_finalized_canceled_intent(outputs, recommendation)
    original = ExecutionStore.reconciliation_ledger
    reads = 0

    def alternating_ledger(execution: ExecutionStore):
        nonlocal reads
        reads += 1
        ledger = original(execution)
        if reads == 1:
            return ledger
        state = ledger.order_states[0]
        return replace(
            ledger,
            filled_intent_ids=frozenset((state.intent_id,)),
            order_states=(
                replace(
                    state,
                    terminal_event_types=(BrokerOrderEventType.FILL,),
                    complete_fill=True,
                    has_fill_evidence=True,
                ),
            ),
        )

    monkeypatch.setattr(ExecutionStore, "reconciliation_ledger", alternating_ledger)

    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    paper = {item.item_id: item.value for item in snapshot.workspaces.paper.items}
    assert reads == 1
    assert paper["day.paper.NVDA"] == "submitted · reconciled"


def test_dashboard_closes_day_trade_only_from_canonical_hermes_and_finalized_ledger(tmp_path: Path) -> None:
    outputs = _day_outputs(tmp_path)
    recommendation = _eligible_request().thesis
    assert UsDayThesisStore(outputs / "us_day" / "theses").publish_thesis(recommendation)
    intent = _append_finalized_filled_intent(outputs, recommendation)
    _append_canonical_hermes_pair(outputs, intent)

    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    paper = {item.item_id: item.value for item in snapshot.workspaces.paper.items}
    assert paper["day.paper.NVDA"] == "filled · protected · reconciled"
    assert paper["day.paper_exit.NVDA"] == "closed"


@pytest.mark.parametrize(
    "shape",
    ("unlinked", "wrong_intent", "wrong_strategy", "submitted_exit", "late_exit"),
)
def test_dashboard_never_closes_from_shaped_unrelated_hermes(tmp_path: Path, shape: str) -> None:
    outputs = _day_outputs(tmp_path)
    recommendation = _eligible_request().thesis
    assert UsDayThesisStore(outputs / "us_day" / "theses").publish_thesis(recommendation)
    intent = _append_finalized_filled_intent(outputs, recommendation)
    _append_canonical_hermes_pair(outputs, intent, shape=shape)

    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    paper = {item.item_id: item.value for item in snapshot.workspaces.paper.items}
    assert paper["day.paper.NVDA"] == "filled · protected · reconciled"
    assert "day.paper_exit.NVDA" not in paper


def test_corrupt_day_hermes_isolated_from_finalized_paper_projection(tmp_path: Path) -> None:
    outputs = _day_outputs(tmp_path)
    recommendation = _eligible_request().thesis
    assert UsDayThesisStore(outputs / "us_day" / "theses").publish_thesis(recommendation)
    _append_finalized_canceled_intent(outputs, recommendation)
    delivery = outputs / "hermes" / "delivery.sqlite3"
    delivery.parent.mkdir(parents=True)
    delivery.write_bytes(b"not sqlite")

    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    paper = {item.item_id: item for item in snapshot.workspaces.paper.items}
    assert snapshot.workspaces.paper.state in {"populated", "stale"}
    assert paper["day.paper_source"].state == "corrupt"
    assert "paper.daily_pnl" in paper


def _day_outputs(tmp_path: Path) -> Path:
    outputs = tmp_path / "outputs"
    (outputs / "us_day").mkdir(parents=True, mode=0o700)
    return outputs


def _seed_versioned_task(
    outputs: Path,
    champion_id: str,
    *,
    shadows: tuple[DayAgentVersionView, ...] = (),
    hypothesis: str = "risk_on",
) -> _VersionReader:
    task = day_task(task_id="task-20260820-NVDA").model_copy(
        update={
            "current_hypothesis": hypothesis,
            "created_at": NOW - dt.timedelta(minutes=2),
            "updated_at": NOW - dt.timedelta(minutes=1),
        }
    )
    with DayAgentTaskStore(outputs / "us_day" / "day_agent.sqlite3").writer() as writer:
        assert writer.create_task(task)
    champion = DayAgentVersionView(
        version_id=champion_id,
        deployment_state="champion",
        task_id=task.task_id,
        observed_at=NOW - dt.timedelta(minutes=1),
    )
    return _VersionReader((champion, *shadows))


def _publish_close_report(outputs: Path) -> None:
    _publish_report(outputs, _payload(), NOW)


def _publish_close_reports_in_source_order(outputs: Path) -> None:
    prior_at = NOW - dt.timedelta(days=1)
    prior = _report_with_time(_payload(session_date=prior_at.date()), prior_at)
    _, created = publish_market_close_report(outputs / "us_day" / "close_reports", prior)
    assert created
    _publish_report(
        outputs,
        _payload(
            session_date=NOW.date(),
            watermark_id="c" * 64,
            cumulative_lineage_report_ids=(prior.report_id,),
            cumulative_actual_return=0.008016,
            cumulative_modeled_return=0.012036,
        ),
        NOW,
    )
    _publish_report(outputs, _payload(MarketId.KR_EQUITIES), NOW + dt.timedelta(minutes=1))


def _publish_report(outputs: Path, payload: MarketCloseReportPayload, finalized_at: dt.datetime) -> None:
    _, created = publish_market_close_report(
        outputs / "us_day" / "close_reports", _report_with_time(payload, finalized_at)
    )
    assert created


def _report_with_time(payload: MarketCloseReportPayload, finalized_at: dt.datetime) -> MarketCloseReport:
    timed = payload.model_copy(
        update={
            "finalized_at": finalized_at,
            "watermark": payload.watermark.model_copy(
                update={"finalized_through": finalized_at - dt.timedelta(minutes=1)}
            ),
        }
    )
    return _report(timed)


def _append_change(
    store: UsDayThesisStore,
    thesis: UsDayTradeThesis,
    kind: ThesisChangeKind = ThesisChangeKind.HOLD,
    note: str = "hold",
) -> None:
    change = UsDayThesisChange.create(
        thesis_id=thesis.thesis_id,
        parent_event_id=thesis.thesis_id,
        kind=kind,
        occurred_at=thesis.observed_at + dt.timedelta(seconds=1),
        note=note,
    )
    assert store.publish_change(change)


def _append_finalized_canceled_intent(outputs: Path, thesis: UsDayTradeThesis) -> None:
    created_at = dt.datetime(2026, 7, 25, 13, 30, tzinfo=dt.UTC)
    intent = PaperOrderIntent(
        intent_id=IntentId(thesis.thesis_id),
        strategy_id="day_orb",
        strategy_version="day-v1",
        symbol="NVDA",
        created_at=created_at,
        side=PaperOrderSide.BUY,
        entry_limit=200.05,
        stop=199.5,
        target_1r=200.60,
        target_2r=201.15,
    )
    raw_update = json.dumps(
        {
            "stream": "trade_updates",
            "data": {
                "event": "canceled",
                "event_id": "day-canceled-order",
                "timestamp": "2026-07-25T13:35:00Z",
                "order": {
                    "id": "day-canceled-order",
                    "client_order_id": thesis.thesis_id,
                    "asset_class": "us_equity",
                    "symbol": "NVDA",
                    "side": "buy",
                    "status": "canceled",
                    "qty": "1",
                    "filled_qty": "0",
                    "filled_avg_price": None,
                    "limit_price": "200.05",
                    "time_in_force": "day",
                    "extended_hours": False,
                    "updated_at": "2026-07-25T13:35:00Z",
                },
            },
        }
    )
    execution = ExecutionStore(outputs / "paper" / "execution.sqlite3")
    with execution.writer() as writer:
        assert writer.bind_account(FINGERPRINT, created_at)
        assert writer.save_intent(intent, quantity=1)
        assert writer.append_trade_update(
            parse_alpaca_trade_update(raw_update),
            account_fingerprint=FINGERPRINT,
            connection_epoch="dashboard-day-test",
            received_at=created_at + dt.timedelta(minutes=5),
        )
        assert writer.append_paper_stream_recovery(
            PaperStreamRecoveryObservation(
                account_fingerprint=FINGERPRINT,
                connection_epoch="dashboard-day-finalized",
                started_at=dt.datetime(2026, 7, 25, 19, 39, tzinfo=dt.UTC),
                completed_at=dt.datetime(2026, 7, 25, 19, 40, tzinfo=dt.UTC),
                snapshot_json='{"orders":[],"positions":[]}',
                execution_detail_complete=True,
            )
        )
        assert writer.save_paper_safety_plan(
            safety_plan(PaperSafetyPhase.ENTRY_CUTOFF, dt.datetime(2026, 7, 25, 19, 45, tzinfo=dt.UTC))
        )
        assert writer.save_paper_safety_plan(
            safety_plan(PaperSafetyPhase.EOD_FLATTEN, dt.datetime(2026, 7, 25, 19, 50, tzinfo=dt.UTC))
        )
    identity = execution.ledger_snapshot_identity()
    append_daily_snapshot(
        outputs,
        complete=True,
        source_generation=identity.generation,
        source_sha256=identity.sha256,
    )
    assert publish_finalized_paper_terminal(
        outputs,
        finalized_snapshot(source_generation=identity.generation, source_sha256=identity.sha256),
        execution,
    )


def _replacement_intent() -> PaperOrderIntent:
    return PaperOrderIntent(
        intent_id=IntentId("f" * 64),
        strategy_id="day_orb",
        strategy_version="day-v1",
        symbol="MSFT",
        created_at=dt.datetime(2026, 7, 25, 13, 31, tzinfo=dt.UTC),
        side=PaperOrderSide.BUY,
        entry_limit=10.0,
        stop=9.5,
        target_1r=10.5,
        target_2r=11.0,
    )


def _append_unrelated_hermes_exit(outputs: Path, thesis_id: str) -> None:
    event = build_hermes_delivery_event(
        kind=HermesDeliveryKind.EXIT,
        source_event_id="unrelated-day-exit",
        market_id="us_equities",
        lane_id="intraday_momentum",
        occurred_at=NOW,
        payload_sha256="e" * 64,
        rendered_text="unrelated exit",
        evidence_refs=(f"intent:{thesis_id}",),
    )
    with HermesDeliveryStore(outputs / "hermes" / "delivery.sqlite3").writer() as writer:
        assert writer.append_event(event).inserted


def _append_finalized_filled_intent(outputs: Path, thesis: UsDayTradeThesis) -> PaperOrderIntent:
    created_at = dt.datetime(2026, 7, 25, 13, 30, tzinfo=dt.UTC)
    intent = PaperOrderIntent(
        intent_id=IntentId(thesis.thesis_id),
        strategy_id="day_orb",
        strategy_version="day-v1",
        symbol="NVDA",
        created_at=created_at,
        side=PaperOrderSide.BUY,
        entry_limit=200.05,
        stop=199.5,
        target_1r=200.60,
        target_2r=201.15,
    )
    raw_update = json.dumps(
        {
            "stream": "trade_updates",
            "data": {
                "event": "fill",
                "event_id": "day-filled-order",
                "timestamp": "2026-07-25T13:35:00Z",
                "order": {
                    "id": "day-filled-order",
                    "client_order_id": thesis.thesis_id,
                    "asset_class": "us_equity",
                    "symbol": "NVDA",
                    "side": "buy",
                    "status": "filled",
                    "qty": "1",
                    "filled_qty": "1",
                    "filled_avg_price": "200.05",
                    "limit_price": "200.05",
                    "time_in_force": "day",
                    "extended_hours": False,
                    "updated_at": "2026-07-25T13:35:00Z",
                },
                "execution_id": "day-fill-execution",
                "qty": "1",
                "price": "200.05",
                "position_qty": "1",
            },
        }
    )
    plan = ProtectiveOcoExitPlan(
        client_order_id=ProtectiveOcoClientOrderId("protect-day-" + "a" * 32),
        parent_intent_id=intent.intent_id,
        symbol=intent.symbol,
        side=PaperOrderSide.SELL,
        quantity=1,
        take_profit_limit=Decimal("201.15"),
        stop_price=Decimal("199.5"),
    )
    plan_key = protective_oco_plan_key(plan)
    mutation = PaperMutationIntent(
        account_fingerprint=FINGERPRINT,
        created_at=created_at + dt.timedelta(minutes=7),
        operation=PaperMutationOperation.SUBMIT_PROTECTIVE_OCO,
        protective_plan_key=plan_key,
        safety_plan_key=None,
        action_sequence=None,
        request_sha256="c" * 64,
        symbol=intent.symbol,
        broker_order_id=None,
        side=PaperOrderSide.SELL,
        quantity=Decimal(1),
    )
    mutation_key = paper_mutation_key(mutation)
    execution = ExecutionStore(outputs / "paper" / "execution.sqlite3")
    with execution.writer() as writer:
        assert writer.bind_account(FINGERPRINT, created_at)
        assert writer.save_intent(intent, quantity=1)
        assert writer.append_trade_update(
            parse_alpaca_trade_update(raw_update),
            account_fingerprint=FINGERPRINT,
            connection_epoch="dashboard-day-filled",
            received_at=created_at + dt.timedelta(minutes=5),
        )
        assert writer.save_protective_oco_plan(plan, created_at + dt.timedelta(minutes=6))
        assert writer.save_paper_mutation_intent(mutation)
        assert writer.append_paper_mutation_event(
            mutation_key,
            PaperMutationEvent(
                1,
                created_at + dt.timedelta(minutes=8),
                PaperMutationEventType.ATTEMPTED,
                None,
                None,
                None,
                "d" * 64,
            ),
        )
        assert writer.append_paper_mutation_event(
            mutation_key,
            PaperMutationEvent(
                1,
                created_at + dt.timedelta(minutes=9),
                PaperMutationEventType.ACKNOWLEDGED,
                "protective-request",
                200,
                BrokerOrderId("protective-parent"),
                "e" * 64,
            ),
        )
        assert writer.append_paper_stream_recovery(
            PaperStreamRecoveryObservation(
                account_fingerprint=FINGERPRINT,
                connection_epoch="dashboard-day-finalized",
                started_at=dt.datetime(2026, 7, 25, 19, 39, tzinfo=dt.UTC),
                completed_at=dt.datetime(2026, 7, 25, 19, 40, tzinfo=dt.UTC),
                snapshot_json='{"orders":[],"positions":[]}',
                execution_detail_complete=True,
            )
        )
        assert writer.save_paper_safety_plan(
            safety_plan(PaperSafetyPhase.ENTRY_CUTOFF, dt.datetime(2026, 7, 25, 19, 45, tzinfo=dt.UTC))
        )
        assert writer.save_paper_safety_plan(
            safety_plan(PaperSafetyPhase.EOD_FLATTEN, dt.datetime(2026, 7, 25, 19, 50, tzinfo=dt.UTC))
        )
    identity = execution.ledger_snapshot_identity()
    append_daily_snapshot(outputs, complete=True, source_generation=identity.generation, source_sha256=identity.sha256)
    assert publish_finalized_paper_terminal(
        outputs,
        finalized_snapshot(source_generation=identity.generation, source_sha256=identity.sha256),
        execution,
    )
    return intent


def _append_canonical_hermes_pair(
    outputs: Path,
    intent: PaperOrderIntent,
    *,
    shape: str | None = None,
) -> None:
    actionable_source = "us-day-actionable-canonical"
    intent_ref = f"intent:{intent.intent_id}"
    exit_ref = "intent:unrelated" if shape == "wrong_intent" else intent_ref
    exit_strategy = "unrelated-version" if shape == "wrong_strategy" else intent.strategy_version
    exit_status = "submitted" if shape == "submitted_exit" else "completed"
    exit_at = (
        dt.datetime(2026, 7, 25, 20, 6, tzinfo=dt.UTC)
        if shape == "late_exit"
        else dt.datetime(2026, 7, 25, 19, 55, tzinfo=dt.UTC)
    )
    records = (
        HermesProjectionRecord(
            source_event_id=actionable_source,
            root_source_event_id=None,
            kind=HermesDeliveryKind.ACTIONABLE,
            market_id="us_equities",
            agent_family="day_trading",
            lane_id="intraday_momentum",
            strategy_version=intent.strategy_version,
            instrument_id=intent.symbol,
            occurred_at=dt.datetime(2026, 7, 25, 13, 31, tzinfo=dt.UTC),
            status="current_quote_validated",
            evidence_refs=(intent_ref,),
            rendered_text="canonical actionable",
            payload_sha256="a" * 64,
        ),
        HermesProjectionRecord(
            source_event_id="us-day-terminal-canonical",
            root_source_event_id=None if shape == "unlinked" else actionable_source,
            kind=HermesDeliveryKind.EXIT,
            market_id="us_equities",
            agent_family="day_trading",
            lane_id="intraday_momentum",
            strategy_version=exit_strategy,
            instrument_id=intent.symbol,
            occurred_at=exit_at,
            status=exit_status,
            evidence_refs=(exit_ref,),
            rendered_text="canonical exit",
            payload_sha256="b" * 64,
        ),
    )
    with HermesDeliveryStore(outputs / "hermes" / "delivery.sqlite3").writer() as writer:
        result = project_outcomes(records, writer)
    assert result.inserted == 2


def _no_trade(observed_at: dt.datetime, *, index: int = 0) -> UsDayTradeThesis:
    return UsDayTradeThesis.create(
        decision=DayTradeDecision.NO_TRADE,
        situation_id=f"{index + 1:064x}",
        agent_version_id="a" * 64,
        playbook_id="leader_breakout",
        theme_id="c" * 64,
        catalyst_event_id="d" * 64,
        flow_inference_kind=None,
        theme_name="semiconductor_infrastructure",
        symbol=None,
        entry_price=None,
        stop_price=None,
        targets=(),
        invalidation_rule="entry conditions are absent.",
        confidence_bps=2500,
        evidence_refs=(),
        observed_at=observed_at,
        valid_until=observed_at + dt.timedelta(minutes=1),
        reason_code="setup_not_confirmed",
    )

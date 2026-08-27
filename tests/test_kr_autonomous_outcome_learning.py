from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from tests.test_kis_kr_market_projection import _json_body, _minute_row
from tests.test_kr_autonomous_trade_planner import _request
from trading_agent.autonomous_memory_models import AutonomousMemoryScope
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.kis_kr_market_models import KisKrMarketReceipt, KisKrMarketReceiptKind
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kr_autonomous_operator_paths import KrAutonomousOperatorPaths
from trading_agent.kr_autonomous_outcome_learning import observe_kr_autonomous_outcomes
from trading_agent.kr_autonomous_outcome_models import (
    KrAutonomousOutcomeMemory,
    KrLoopEngineerEvidenceBundle,
    KrOutcomeExecutionState,
    KrOutcomeHorizon,
)
from trading_agent.kr_autonomous_trade_models import (
    KrAutonomousRejected,
    KrAutonomousTradeOutcome,
    KrCriticReason,
    KrTradeRecommendation,
    event_id,
)
from trading_agent.kr_autonomous_trade_planner import plan_kr_autonomous_trade
from trading_agent.kr_autonomous_trade_store import KrAutonomousTradeStore
from trading_agent.kr_social_signal_models import KrSocialSignal, _signal_id
from trading_agent.kr_social_signal_store import KrSocialSignalStore
from trading_agent.kr_virtual_position_engine import advance_kr_virtual_position, arm_kr_virtual_position
from trading_agent.kr_virtual_position_store import KrVirtualPositionStore
from trading_agent.signal_contract_models import EvidenceRef

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 8, 26, 13, 4, 4, tzinfo=KST)


def test_terminal_virtual_outcome_appends_only_new_horizon_versions(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    request = _request()
    recommendation = plan_kr_autonomous_trade(request)
    assert isinstance(recommendation, KrTradeRecommendation)
    assert KrSocialSignalStore(paths.social_signal_database).append(request.social_signal)
    assert KrAutonomousTradeStore(paths.trade_database).append(recommendation)
    armed = arm_kr_virtual_position(recommendation, recommendation.timestamp)
    stopped = advance_kr_virtual_position(
        recommendation,
        armed,
        (_collision_bar(recommendation),),
        NOW + dt.timedelta(minutes=2, seconds=1),
    )[0]
    positions = KrVirtualPositionStore(paths.position_database)
    assert positions.append(armed)
    assert positions.append(stopped)

    initial = observe_kr_autonomous_outcomes(paths, now=NOW + dt.timedelta(minutes=2, seconds=1))
    _append_minute_receipt(paths, minutes=6)
    five = observe_kr_autonomous_outcomes(paths, now=NOW + dt.timedelta(minutes=6, seconds=1))
    replay = observe_kr_autonomous_outcomes(paths, now=NOW + dt.timedelta(minutes=6, seconds=1))
    _append_minute_receipt(paths, minutes=16)
    fifteen = observe_kr_autonomous_outcomes(paths, now=NOW + dt.timedelta(minutes=16, seconds=1))

    history = AutonomousMemoryStore(paths.memory_database).reader().history(initial.memory_keys[0])
    payloads = tuple(KrAutonomousOutcomeMemory.model_validate_json(item.summary) for item in history)
    assert initial.inserted_memories == five.inserted_memories == fifteen.inserted_memories == 1
    assert replay.inserted_memories == 0
    assert tuple(item.version for item in history) == (1, 2, 3)
    assert all(item.scope is AutonomousMemoryScope.MARKET for item in history)
    assert all(item.execution_state is KrOutcomeExecutionState.VIRTUAL_STOPPED for item in payloads)
    assert payloads[0].horizons == ()
    assert tuple(item.horizon for item in payloads[1].horizons) == (KrOutcomeHorizon.MINUTES_5,)
    assert tuple(item.horizon for item in payloads[2].horizons) == (
        KrOutcomeHorizon.MINUTES_5,
        KrOutcomeHorizon.MINUTES_15,
    )
    assert "symbol:005930" in history[-1].subject_refs
    assert "market:current" in history[-1].subject_refs
    assert "session:continuous" in history[-1].subject_refs
    assert f"source-cluster:{request.social_signal.independent_source_cluster_ids[0]}" in history[-1].subject_refs
    assert set(recommendation.evidence_refs) <= set(payloads[-1].evidence_refs)


def test_third_repeated_critic_failure_creates_replay_safe_bundle(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    previous: str | None = None
    for marker in range(3):
        request = _request()
        task_id = f"{marker + 1:064x}"
        signal = _signal_for_task(request.social_signal, task_id)
        assert KrSocialSignalStore(paths.social_signal_database).append(signal)
        rejected = KrAutonomousRejected.model_construct(
            event_id="",
            plan_id=f"{marker + 11:064x}",
            previous_event_id=previous,
            timestamp=NOW + dt.timedelta(seconds=marker),
            task_id=task_id,
            thesis_id=f"{marker + 21:064x}",
            symbol=signal.symbol,
            theme=signal.theme,
            reason_codes=(KrCriticReason.CLUSTER_COUNT,),
            critic_verdict_id=f"{marker + 31:064x}",
            next_wake_at=NOW + dt.timedelta(minutes=1, seconds=marker),
        )
        rejected = KrAutonomousRejected.model_validate(
            rejected.model_copy(update={"event_id": event_id(rejected)}).model_dump(mode="python")
        )
        assert rejected.outcome is KrAutonomousTradeOutcome.REJECTED
        assert KrAutonomousTradeStore(paths.trade_database).append(rejected)
        previous = rejected.event_id

    result = observe_kr_autonomous_outcomes(paths, now=NOW + dt.timedelta(minutes=2))
    replay = observe_kr_autonomous_outcomes(paths, now=NOW + dt.timedelta(minutes=2))
    bundle_records = tuple(
        record
        for key in result.bundle_keys
        for record in AutonomousMemoryStore(paths.memory_database).reader().history(key)
    )

    assert result.inserted_memories == 3
    assert result.inserted_bundles == 1
    assert replay.inserted_memories == replay.inserted_bundles == 0
    assert len(bundle_records) == 1
    assert bundle_records[0].scope is AutonomousMemoryScope.SELF_IMPROVEMENT
    bundle = KrLoopEngineerEvidenceBundle.model_validate_json(bundle_records[0].summary)
    assert len(bundle.source_memory_ids) == 3
    assert bundle.code_mutation_authority is False
    assert bundle.failure_code == "critic_cluster_count"


def _paths(tmp_path: Path) -> KrAutonomousOperatorPaths:
    root = tmp_path / "autonomous-supervisor"
    kr = root / "kr-v1"
    return KrAutonomousOperatorPaths(
        task_database=root / "tasks.sqlite3",
        memory_database=root / "memory.sqlite3",
        social_signal_database=kr / "signals.sqlite3",
        trade_database=kr / "trades.sqlite3",
        position_database=kr / "positions.sqlite3",
        market_receipt_root=kr / "market-receipts",
    )


def _signal_for_task(signal: KrSocialSignal, task_id: str) -> KrSocialSignal:
    draft = signal.model_copy(update={"task_id": task_id, "signal_id": ""})
    return KrSocialSignal.model_validate(
        draft.model_copy(update={"signal_id": _signal_id(draft)}).model_dump(mode="python")
    )


def _collision_bar(recommendation: KrTradeRecommendation):
    from trading_agent.kr_theme_day_setup_progress import KrCompletedMinuteBar

    start = recommendation.timestamp.astimezone(KST).replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    return KrCompletedMinuteBar(
        symbol=recommendation.symbol,
        start_at=start,
        end_at=start + dt.timedelta(minutes=1),
        observed_at=start + dt.timedelta(minutes=1, seconds=1),
        open=recommendation.entry,
        high=recommendation.targets[1],
        low=recommendation.stop,
        close=recommendation.entry,
        volume=100,
        trading_value_krw=recommendation.entry * 100,
        evidence_ref=EvidenceRef(
            namespace="kr/outcome-test",
            record_id="collision",
            observed_at=start + dt.timedelta(minutes=1, seconds=1),
        ),
    )


def _append_minute_receipt(paths: KrAutonomousOperatorPaths, *, minutes: int) -> None:
    rows = []
    cumulative = Decimal(0)
    for offset in range(-2, minutes):
        started = NOW.replace(second=0, microsecond=0) + dt.timedelta(minutes=offset)
        price = Decimal(103 + max(offset, 0))
        cumulative += price * 100
        row = _minute_row(
            started.strftime("%H%M%S"),
            str(price),
            str(price + 1),
            str(price - 1),
            str(price),
            "100",
            str(cumulative),
        )
        row["stck_bsop_date"] = NOW.strftime("%Y%m%d")
        rows.append(row)
    received_at = NOW.replace(second=0, microsecond=0) + dt.timedelta(minutes=minutes, seconds=1)
    receipt = KisKrMarketReceipt(
        kind=KisKrMarketReceiptKind.MINUTE_BARS,
        symbol="005930",
        received_at=received_at,
        status_code=200,
        content_type="application/json",
        raw_payload=_json_body({"output1": {}, "output2": rows}),
    )
    assert KisKrMarketReceiptStore(paths.market_receipt_root / "005930.sqlite3").append(receipt)

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from trading_agent.dashboard_paper_finalized_terminal_writer import publish_finalized_paper_terminal
from trading_agent.day_strategy_capsule_models import StrategyCapsule
from trading_agent.execution_store import ExecutionStore
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.lane_contract_keys import experiment_scope_key, lane_manifest_key
from trading_agent.lane_contract_models import LaneDailySnapshot
from trading_agent.lane_defaults import CURRENT_INTRADAY_EXPERIMENT_SCOPES, INTRADAY_MANIFEST
from trading_agent.lane_identity_models import LaneId
from trading_agent.lane_registry_store import LaneRegistryStore
from trading_agent.paper_execution_models import AccountFingerprint
from trading_agent.paper_safety_models import PaperSafetyPhase, PaperSafetyPlan
from trading_agent.paper_stream_recovery_models import PaperStreamRecoveryObservation
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.research_identity_models import StrategyLaneRef
from trading_agent.us_day_agent_cli_bindings import (
    ProductionManifest,
    ReviewedUsDayStrategyManifest,
)
from trading_agent.us_day_thesis_models import UsDayPlaybook
from trading_agent.us_forward_shadow_services import UsForwardShadowServices

CLOSE_AT = dt.datetime(2026, 8, 20, 20, 5, tzinfo=dt.UTC)
_FINGERPRINT = AccountFingerprint("b" * 64)


def publish_finalized_paper(outputs: Path, strategy_version: str) -> None:
    execution = ExecutionStore(outputs / "paper" / "execution.sqlite3")
    with execution.writer() as writer:
        assert writer.bind_account(_FINGERPRINT, CLOSE_AT - dt.timedelta(hours=6))
        assert writer.append_paper_stream_recovery(
            PaperStreamRecoveryObservation(
                account_fingerprint=_FINGERPRINT,
                connection_epoch="cli-post-close",
                started_at=CLOSE_AT - dt.timedelta(minutes=26),
                completed_at=CLOSE_AT - dt.timedelta(minutes=25),
                snapshot_json='{"orders":[],"positions":[]}',
                execution_detail_complete=True,
            )
        )
        assert writer.save_paper_safety_plan(_safety(PaperSafetyPhase.ENTRY_CUTOFF, 20))
        assert writer.save_paper_safety_plan(_safety(PaperSafetyPhase.EOD_FLATTEN, 10))
    identity = execution.ledger_snapshot_identity()
    registry = LaneRegistryStore(outputs / "lane_control" / "lane_registry.sqlite3")
    scope = CURRENT_INTRADAY_EXPERIMENT_SCOPES[0]
    snapshot = LaneDailySnapshot(
        lane_id=LaneId.INTRADAY_MOMENTUM,
        session_date=CLOSE_AT.date(),
        finalized_at=CLOSE_AT,
        manifest_key=lane_manifest_key(INTRADAY_MANIFEST),
        experiment_scope_keys=(experiment_scope_key(scope),),
        source_ledger_generation=identity.generation,
        source_ledger_sha256=identity.sha256,
        champion_strategy_versions=(strategy_version,),
        data_quality_complete=True,
        allocation_eligible=False,
        incidents=(),
        conservative_equity=Decimal("30000"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        planned_open_risk=Decimal("0"),
        open_order_count=0,
        open_position_count=0,
    )
    with registry.writer() as writer:
        _ = writer.register_manifest(INTRADAY_MANIFEST)
        _ = writer.register_experiment_scope(scope)
        assert writer.append_daily_snapshot(snapshot)
    delivery = HermesDeliveryStore(outputs / "hermes" / "delivery.sqlite3")
    with delivery.writer():
        pass
    assert publish_finalized_paper_terminal(outputs, snapshot, execution)


def publish_close_manifest(
    root: Path,
    repository: Path,
    forward: UsForwardShadowServices,
    capsule: StrategyCapsule,
    lane: StrategyLaneRef,
    playbook: UsDayPlaybook,
    loop_inputs: Path,
    patch_response: Path,
) -> Path:
    strategy_manifest = root / "strategy.json"
    assert publish_private_immutable_text(
        strategy_manifest,
        ReviewedUsDayStrategyManifest(
            capsule_id=capsule.capsule_id,
            strategy_lane=lane,
            playbook=playbook,
            reviewed=True,
        ).model_dump_json(),
    )
    path = root / "production.json"
    assert publish_private_immutable_text(
        path,
        ProductionManifest(
            repository=repository,
            review_ledger=root / "review.sqlite3",
            experiment_ledger=forward.ledger.path,
            strategy_manifest=strategy_manifest,
            lane_registry=root / "paper-lane.sqlite3",
            arm_database=root / "arms.sqlite3",
            arm_signing_key=root / "arm.key",
            execution_database=root / "paper-execution.sqlite3",
            delivery_database=root / "delivery.sqlite3",
            session_root=root / "sessions",
            safety_arm_request_id="a" * 64,
            generated_artifact_root=forward.generated_artifacts.root,
            forward_shadow_artifact_root=forward.shadow_artifacts.root,
            loop_task_root=root / "loop-tasks",
            loop_inputs=loop_inputs,
            patch_model_response=patch_response,
        ).model_dump_json(),
    )
    return path


def _safety(phase: PaperSafetyPhase, minutes_before_close: int) -> PaperSafetyPlan:
    return PaperSafetyPlan(
        account_fingerprint=_FINGERPRINT,
        observed_at=CLOSE_AT - dt.timedelta(minutes=minutes_before_close),
        session_date=CLOSE_AT.date(),
        phase=phase,
        mark_to_market_daily_pnl=Decimal("0"),
        conservative_daily_pnl=Decimal("0"),
        actions=(),
    )


__all__ = ("CLOSE_AT", "publish_close_manifest", "publish_finalized_paper")

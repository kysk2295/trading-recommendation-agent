from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from decimal import Decimal

from tests.test_day_learning_report_models import NOW, SHA_A
from trading_agent.dashboard_paper_finalized_terminal import (
    FinalizedPaperAuthority,
    FinalizedPaperTerminalReceipt,
)
from trading_agent.dashboard_us_day_paper import FinalizedPaperProjectionBundle
from trading_agent.day_agent_loop_engineer import ProposedAgentChange
from trading_agent.day_agent_version_models import (
    AgentChangeKind,
    AgentDeploymentState,
    AgentModelRoleBinding,
    AgentVersion,
    LeaderRankingFeature,
    LeaderRankingPatch,
    build_agent_version,
)
from trading_agent.day_learning_report_models import (
    DayDecisionDiagnostic,
    DayDecisionOutcome,
    DayDecisionStage,
)
from trading_agent.execution_ledger_identity import ExecutionLedgerSnapshotIdentity
from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.execution_schema import StoredIntent
from trading_agent.lane_contract_keys import lane_daily_snapshot_key, lane_manifest_key
from trading_agent.lane_contract_models import LaneDailySnapshot
from trading_agent.lane_defaults import INTRADAY_MANIFEST
from trading_agent.lane_identity_models import LaneId
from trading_agent.paper_execution_models import IntentId, PaperOrderSide
from trading_agent.us_day_thesis_models import UsDayTradeThesis

SESSION = dt.date(2026, 8, 20)


def diagnostics(*, leader_score: float = 0.1) -> tuple[DayDecisionDiagnostic, ...]:
    return tuple(
        DayDecisionDiagnostic(
            stage=stage,
            outcome=(
                DayDecisionOutcome.REFUTED
                if stage is DayDecisionStage.LEADER_SELECTION
                else DayDecisionOutcome.SUPPORTED
            ),
            score=leader_score if stage is DayDecisionStage.LEADER_SELECTION else 0.8,
            evidence_ids=(SHA_A,),
            reason_codes=("leader_rank_late",) if stage is DayDecisionStage.LEADER_SELECTION else ("supported",),
        )
        for stage in DayDecisionStage
    )


def champion() -> AgentVersion:
    return build_agent_version(
        model_role_bindings=(AgentModelRoleBinding(role="reasoning", model_id="reasoner-v1"),),
        prompt_sha256="1" * 64,
        tool_policy_sha256="2" * 64,
        memory_retrieval_policy_sha256="3" * 64,
        playbook_ids=("4" * 64,),
        parent_version_id=None,
        creation_evidence_ids=(SHA_A,),
        deployment_state=AgentDeploymentState.CHAMPION,
        task_id="task-20260820-NVDA",
        created_at=NOW,
        created_session_date=SESSION,
    )


def paper_bundle(thesis: UsDayTradeThesis) -> FinalizedPaperProjectionBundle:
    finalized_at = dt.datetime(2026, 8, 20, 20, 5, tzinfo=dt.UTC)
    identity = ExecutionLedgerSnapshotIdentity(1, "e" * 64)
    snapshot = LaneDailySnapshot(
        lane_id=LaneId.INTRADAY_MOMENTUM,
        session_date=SESSION,
        finalized_at=finalized_at,
        manifest_key=str(lane_manifest_key(INTRADAY_MANIFEST)),
        experiment_scope_keys=("f" * 64,),
        source_ledger_generation=identity.generation,
        source_ledger_sha256=identity.sha256,
        champion_strategy_versions=(thesis.agent_version_id,),
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
    receipt = FinalizedPaperTerminalReceipt(
        lane_id=snapshot.lane_id,
        session_date=snapshot.session_date,
        manifest_key=snapshot.manifest_key,
        snapshot_key=str(lane_daily_snapshot_key(snapshot)),
        source_ledger_generation=identity.generation,
        source_ledger_sha256=identity.sha256,
        strategy_versions=snapshot.champion_strategy_versions,
        recovery_snapshot_sha256="b" * 64,
        observed_at=finalized_at,
    )
    authority = FinalizedPaperAuthority(
        receipt=receipt,
        safe_ref=hashlib.sha256(receipt.model_dump_json(exclude_none=False).encode()).hexdigest(),
    )
    assert thesis.symbol is not None
    assert thesis.entry_price is not None
    assert thesis.stop_price is not None
    intent_id = IntentId(thesis.thesis_id)
    intent = StoredIntent(
        intent_id=intent_id,
        strategy_id="day_agent",
        strategy_version=thesis.agent_version_id,
        symbol=thesis.symbol,
        created_at=(thesis.observed_at + dt.timedelta(seconds=1)).isoformat(),
        side=PaperOrderSide.BUY,
        entry_limit=thesis.entry_price,
        stop=thesis.stop_price,
        target_1r=thesis.targets[0].price,
        target_2r=thesis.targets[1].price,
        quantity=1,
    )
    ledger = ReconciliationLedger(
        intents=(intent,),
        unresolved_intent_ids=frozenset(),
        account_fingerprint=None,
        filled_intent_ids=frozenset({intent_id}),
    )
    return FinalizedPaperProjectionBundle(ledger, identity, snapshot, authority, (), True)


@dataclass(frozen=True, slots=True)
class LeaderAuthor:
    def propose(self, stage: DayDecisionStage, champion: AgentVersion) -> ProposedAgentChange:
        assert stage is DayDecisionStage.LEADER_SELECTION
        assert champion.deployment_state is AgentDeploymentState.CHAMPION
        return ProposedAgentChange(
            patch=LeaderRankingPatch(
                kind=AgentChangeKind.LEADER_RANKING_POLICY,
                feature=LeaderRankingFeature.RELATIVE_VOLUME,
                weight_bps=2_500,
            ),
        )


__all__ = ("SESSION", "LeaderAuthor", "champion", "diagnostics", "paper_bundle")

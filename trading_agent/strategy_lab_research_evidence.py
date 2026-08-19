from __future__ import annotations

from pathlib import Path
from typing import final

from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.research_agent_cycle_models import (
    ResearchAgentEvidenceV1,
    ResearchAgentTriggerKind,
)
from trading_agent.research_agent_source_common import (
    InvalidResearchAgentSourceError,
    ResearchAgentEvidenceMaterial,
    canonical_model_json,
    require_private_source_file,
)
from trading_agent.strategy_lab_ledger import StrategyLabLedgerError
from trading_agent.strategy_lab_models import STRATEGY_LAB_IDS


@final
class StrategyLabResultSourceAdapter:
    __slots__ = ()

    def collect(self, experiment_ledger: Path) -> tuple[ResearchAgentEvidenceV1, ...]:
        if not experiment_ledger.exists():
            return ()
        try:
            require_private_source_file(experiment_ledger)
            reader = ExperimentLedgerReader(experiment_ledger)
            if not reader.is_initialized():
                raise InvalidResearchAgentSourceError(reason="strategy_lab_ledger_unavailable")
            return tuple(
                ResearchAgentEvidenceMaterial(
                    family="systematic_quant",
                    trigger=ResearchAgentTriggerKind.EXPERIMENT_RESULT,
                    source_key=f"systematic.strategy_lab.{node.node_id}",
                    observed_at=node.body.result.evaluated_at,
                    available_at=node.body.result.evaluated_at,
                    market_id="none",
                    canonical_payload=canonical_model_json(node),
                    subject_refs=(f"strategy_lab_node.{node.node_id}",),
                ).evidence()
                for lab_id in STRATEGY_LAB_IDS
                for node in reader.strategy_lab_trace(lab_id)
            )
        except (
            InvalidExperimentLedgerSourceError,
            InvalidResearchAgentSourceError,
            StrategyLabLedgerError,
            UnsupportedExperimentLedgerSchemaError,
            OSError,
            ValueError,
        ):
            raise InvalidResearchAgentSourceError(reason="strategy_lab_source_invalid") from None


__all__ = ("StrategyLabResultSourceAdapter",)

from __future__ import annotations

from pathlib import Path

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.private_immutable_file import read_private_text
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.strategy_research_hypothesis_factory import SourceHypothesisArtifact
from trading_agent.strategy_research_models import PreregistrationManifest
from trading_agent.strategy_research_runtime_models import StrategyResearchWork
from trading_agent.strategy_research_runtime_source import StrategyResearchWorkQueue
from trading_agent.strategy_research_types import HypothesisStatus


class InvalidStrategyResearchWorkSinkError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class PrivateStrategyResearchWorkSink:
    __slots__ = ("_ledger", "_root")

    def __init__(self, ledger: ExperimentLedgerStore, root: Path) -> None:
        self._ledger = ledger
        self._root = root.expanduser().absolute()

    def persist(self, artifact: SourceHypothesisArtifact) -> bool:
        preregistered = artifact.hypothesis.model_copy(update={"status": HypothesisStatus.PREREGISTERED})
        manifest = PreregistrationManifest.from_hypothesis(
            preregistered,
            preregistered_at=artifact.hypothesis.created_at,
        )
        with self._ledger.writer() as writer:
            _ = writer.register_strategy_research(manifest)
        work = StrategyResearchWork(
            evidence_event_id=f"source-hypothesis:{artifact.hypothesis.hypothesis_id}",
            available_at=artifact.observation.predictor_observed_at,
            maturity_at=artifact.hypothesis.target_matures_at,
            draft=artifact.hypothesis,
            source_receipts=artifact.source_receipts,
        )
        path = self._root / f"{artifact.hypothesis.agent_id.value}.json"
        existing = (
            () if not path.exists() else StrategyResearchWorkQueue.model_validate_json(read_private_text(path)).items
        )
        matches = tuple(item for item in existing if item.evidence_event_id == work.evidence_event_id)
        if matches:
            if matches != (work,):
                raise InvalidStrategyResearchWorkSinkError(reason="strategy_research_work_conflict")
            return False
        items = tuple(sorted((*existing, work), key=lambda item: (item.available_at, item.evidence_event_id)))
        write_private_stable_report(path, StrategyResearchWorkQueue(items=items).model_dump_json() + "\n")
        return True


__all__ = ("InvalidStrategyResearchWorkSinkError", "PrivateStrategyResearchWorkSink")

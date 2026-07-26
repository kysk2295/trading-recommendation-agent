from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, assert_never

from pydantic import ValidationError

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.dashboard_directed_package import (
    FixedDirectedResearchPackage,
    InvalidDirectedResearchPackageError,
    ensure_private_directory,
    require_private_file,
)
from trading_agent.dashboard_directed_research_models import (
    DirectedExperimentSpec,
    DirectedResearchKind,
    DirectedResearchReceipt,
    InvalidDirectedResearchBrokerError,
)
from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.intraday_actual_research import run_intraday_actual_research
from trading_agent.intraday_actual_research_models import (
    IntradayActualResearchPaths,
    IntradayActualResearchRequest,
)
from trading_agent.intraday_research_input_binding_models import IntradayResearchStrategyBinding
from trading_agent.lane_bootstrap import bootstrap_lane_control_plane
from trading_agent.lane_registry_store import LaneRegistryStore
from trading_agent.research_hypothesis_registration import register_research_hypothesis_manifest
from trading_agent.source_driven_hypothesis_queue import (
    project_source_driven_hypothesis_queue,
    publish_source_driven_hypothesis_queue,
)


class AuthoritativeDirectedResearchBroker:
    def __init__(self, *, state_root: Path, source_evidence_root: Path) -> None:
        self._state_root = state_root
        self._source_root = source_evidence_root

    def execute(self, operation: DirectedResearchKind, family_id: AgentFamilyId) -> bytes:
        try:
            package = FixedDirectedResearchPackage.from_source_root(self._source_root)
            match operation:
                case "research" | "analysis":
                    receipt = self._query(operation, family_id, package)
                case "hypothesis":
                    receipt = self._register(family_id, package)
                case "experiment":
                    receipt = self._experiment(family_id, package)
                case unexpected:
                    assert_never(unexpected)
        except (
            InvalidDirectedResearchBrokerError,
            InvalidDirectedResearchPackageError,
            OSError,
            RuntimeError,
            ValidationError,
            ValueError,
        ) as error:
            raise InvalidDirectedResearchBrokerError from error
        return receipt.model_dump_json().encode()

    def _query(
        self,
        operation: Literal["research", "analysis"],
        family_id: AgentFamilyId,
        package: FixedDirectedResearchPackage,
    ) -> DirectedResearchReceipt:
        ledger, _ = self._ensure_hypothesis(family_id, package)
        artifact = project_source_driven_hypothesis_queue(ExperimentLedgerReader(ledger.path))
        _, created = publish_source_driven_hypothesis_queue(
            self._family_root(family_id) / operation,
            artifact,
        )
        if not created:
            raise InvalidDirectedResearchBrokerError
        item = _single_item(artifact.snapshot.items)
        return DirectedResearchReceipt(
            operation=operation,
            terminal="completed",
            domain_effects=1,
            evidence_sha256s=(item.card_key, *item.source_keys),
            result_sha256=artifact.snapshot_id,
            summary=f"authoritative {operation} output published",
        )

    def _register(
        self,
        family_id: AgentFamilyId,
        package: FixedDirectedResearchPackage,
    ) -> DirectedResearchReceipt:
        ledger, created = self._ensure_hypothesis(family_id, package)
        if created != 1:
            raise InvalidDirectedResearchBrokerError
        card = _single_item(ExperimentLedgerReader(ledger.path).research_hypothesis_cards())
        return DirectedResearchReceipt(
            operation="hypothesis",
            terminal="completed",
            domain_effects=1,
            evidence_sha256s=card.card.research_source_keys,
            result_sha256=str(card.card_key),
            summary="authoritative hypothesis registered",
        )

    def _experiment(
        self,
        family_id: AgentFamilyId,
        package: FixedDirectedResearchPackage,
    ) -> DirectedResearchReceipt:
        hypothesis_manifest = package.hypothesis_manifest()
        spec = _load_spec(package.experiment_spec())
        entitlement = package.entitlement_contract()
        session_dirs = package.session_directories(spec.session_dates)
        ledger = ExperimentLedgerStore(self._family_root(family_id) / "experiment.sqlite3")
        _ = register_research_hypothesis_manifest(hypothesis_manifest, ledger)
        queue = project_source_driven_hypothesis_queue(ExperimentLedgerReader(ledger.path))
        queue_path, _ = publish_source_driven_hypothesis_queue(
            self._family_root(family_id) / "experiment-queue",
            queue,
        )
        require_private_file(queue_path, self._family_root(family_id))
        item = _single_item(queue.snapshot.items)
        family_root = self._family_root(family_id)
        lane_registry = family_root / "lane-registry.sqlite3"
        _ = bootstrap_lane_control_plane(LaneRegistryStore(lane_registry))
        result = run_intraday_actual_research(
            IntradayActualResearchRequest(
                session_dirs=session_dirs,
                required_session_dates=spec.required_session_dates,
                strategy_bindings=(
                    IntradayResearchStrategyBinding(
                        strategy=spec.strategy,
                        strategy_version=spec.strategy_version,
                        queue_card_key=item.card_key,
                    ),
                ),
                dataset_producer_commit_sha=spec.dataset_producer_commit_sha,
                code_version=spec.code_version,
                registered_at=spec.registered_at,
                observed_at=spec.observed_at,
                minimum_clean_sessions=spec.minimum_clean_sessions,
                minimum_training_sessions=spec.minimum_training_sessions,
                max_sessions=spec.max_sessions,
                max_bars=spec.max_bars,
                per_side_fee_bps=spec.per_side_fee_bps,
                per_side_slippage_bps=spec.per_side_slippage_bps,
                bootstrap_samples=spec.bootstrap_samples,
                rss_limit_gib=spec.rss_limit_gib,
                paths=IntradayActualResearchPaths(
                    dataset_root=family_root / "dataset",
                    binding_root=family_root / "binding",
                    entitlement_contract=entitlement,
                    source_queue_artifact=queue_path,
                    lane_registry=lane_registry,
                    experiment_ledger=ledger.path,
                    artifact_root=family_root / "artifacts",
                    review_root=family_root / "reviews",
                ),
            )
        )
        trials = ExperimentLedgerReader(ledger.path).trials()
        trial = _single_item(trials)
        events = ExperimentLedgerReader(ledger.path).trial_events(trial.registration.trial_id)
        terminal = events[-1].event if events else None
        effects = result.loop.experiment_artifacts_created + result.loop.review_artifacts_created
        if (
            effects < 2
            or terminal is None
            or terminal.event_kind is not TrialEventKind.COMPLETED
            or len(terminal.artifact_sha256s) != 1
        ):
            raise InvalidDirectedResearchBrokerError
        return DirectedResearchReceipt(
            operation="experiment",
            terminal="completed",
            domain_effects=effects,
            evidence_sha256s=(
                queue.snapshot_id,
                result.catalog.catalog_receipt_sha256,
                result.binding.input_sha256,
            ),
            result_sha256=terminal.artifact_sha256s[0],
            summary="authoritative bounded experiment and review completed",
        )

    def _ensure_hypothesis(
        self,
        family_id: AgentFamilyId,
        package: FixedDirectedResearchPackage,
    ) -> tuple[ExperimentLedgerStore, int]:
        ledger = ExperimentLedgerStore(self._family_root(family_id) / "experiment.sqlite3")
        registered = register_research_hypothesis_manifest(
            package.hypothesis_manifest(),
            ledger,
        )
        return ledger, registered.cards_created

    def _family_root(self, family_id: AgentFamilyId) -> Path:
        ensure_private_directory(self._state_root, self._state_root)
        authority_root = self._state_root / "authoritative"
        ensure_private_directory(authority_root, self._state_root)
        root = self._state_root / "authoritative" / family_id
        ensure_private_directory(root, self._state_root)
        return root


def _load_spec(path: Path) -> DirectedExperimentSpec:
    try:
        return DirectedExperimentSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, UnicodeError, ValidationError) as error:
        raise InvalidDirectedResearchBrokerError from error


def _single_item[T](items: tuple[T, ...]) -> T:
    if len(items) != 1:
        raise InvalidDirectedResearchBrokerError
    return items[0]


__all__ = ("AuthoritativeDirectedResearchBroker",)

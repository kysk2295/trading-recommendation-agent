from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import override

from trading_agent.daily_research_contract import (
    CURRENT_COST_MODEL,
    CURRENT_DATA_CONTRACT,
    SHADOW_PORTFOLIO_POLICY,
)
from trading_agent.experiment_ledger_models import ResearchHypothesisCard, StrategyVersionRegistration
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.generated_intraday_research_models import (
    GeneratedIntradayResearchManifest,
    GeneratedStrategySelection,
)
from trading_agent.generated_strategy_artifact import PublishedGeneratedStrategy
from trading_agent.source_driven_hypothesis_queue import (
    load_source_driven_hypothesis_queue,
    project_source_driven_hypothesis_queue,
)
from trading_agent.source_driven_hypothesis_queue_models import (
    HypothesisQueueRoute,
    SourceDrivenHypothesisQueueItem,
)


class GeneratedIntradayRegistrationError(ValueError):
    @override
    def __str__(self) -> str:
        return "generated intraday strategy registration invalid"


def register_generated_intraday_strategy(
    ledger: ExperimentLedgerStore,
    queue_path: Path,
    manifest: GeneratedIntradayResearchManifest,
    selection: GeneratedStrategySelection,
    published: PublishedGeneratedStrategy,
) -> tuple[StrategyVersionRegistration, bool]:
    try:
        reader = ExperimentLedgerReader(ledger.path)
        queue = load_source_driven_hypothesis_queue(queue_path)
        items = tuple(item for item in queue.snapshot.items if item.card_key == selection.queue_card_key)
        cards = tuple(
            stored.card
            for stored in reader.research_hypothesis_cards()
            if str(stored.card_key) == selection.queue_card_key
        )
        if len(items) != 1 or len(cards) != 1:
            raise GeneratedIntradayRegistrationError
        item = items[0]
        card = cards[0]
        registration = _registration(manifest, selection, published, card)
        prior = tuple(
            stored.registration
            for stored in reader.strategy_versions()
            if stored.registration.hypothesis_id == selection.hypothesis_id
        )
        exact_replay = len(prior) == 1 and prior[0] == registration
        if (
            queue.snapshot_id != manifest.source_queue_snapshot_id
            or not _lineage_matches(item, selection, published, card)
            or not _source_matches(published)
            or (queue != project_source_driven_hypothesis_queue(reader) and not exact_replay)
            or (prior and not exact_replay)
            or (
                not prior
                and (
                    item.route is not HypothesisQueueRoute.STRATEGY_DESIGN
                    or item.strategy_versions
                    or item.historical_trial_ids
                )
            )
        ):
            raise GeneratedIntradayRegistrationError
        with ledger.writer() as writer:
            created = writer.register_strategy_version(registration)
        return registration, created
    except GeneratedIntradayRegistrationError:
        raise
    except (OSError, TypeError, ValueError):
        raise GeneratedIntradayRegistrationError from None


def _registration(
    manifest: GeneratedIntradayResearchManifest,
    selection: GeneratedStrategySelection,
    published: PublishedGeneratedStrategy,
    card: ResearchHypothesisCard,
) -> StrategyVersionRegistration:
    artifact = published.artifact
    return StrategyVersionRegistration(
        strategy_id="generated_python",
        strategy_version=selection.strategy_version,
        hypothesis_id=card.hypothesis.hypothesis_id,
        experiment_scope_key=card.hypothesis.experiment_scope_key,
        lane_id=card.hypothesis.primary_lane,
        code_version=artifact.payload.source_sha256,
        parameter_set=artifact.payload.free_parameters,
        data_contract=CURRENT_DATA_CONTRACT,
        cost_model=CURRENT_COST_MODEL,
        portfolio_policy=SHADOW_PORTFOLIO_POLICY,
        source_registered_at=card.hypothesis.source_registered_at,
        ledger_recorded_at=manifest.registered_at,
    )


def _lineage_matches(
    item: SourceDrivenHypothesisQueueItem,
    selection: GeneratedStrategySelection,
    published: PublishedGeneratedStrategy,
    card: ResearchHypothesisCard,
) -> bool:
    artifact = published.artifact
    return (
        item.hypothesis_id == selection.hypothesis_id == artifact.payload.hypothesis_id
        and item.card_key == selection.queue_card_key == artifact.payload.card_key
        and item.source_keys == card.research_source_keys == artifact.payload.research_source_keys
        and artifact.artifact_id == selection.artifact_id
        and artifact.payload.runtime.runtime_fingerprint == selection.runtime_fingerprint
        and artifact.payload.runtime.sandbox_profile_version == selection.sandbox_profile_version
        and card.hypothesis.hypothesis_id == selection.hypothesis_id
    )


def _source_matches(published: PublishedGeneratedStrategy) -> bool:
    source = published.source_path
    metadata = source.lstat()
    return (
        not source.is_symlink()
        and stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and hashlib.sha256(source.read_bytes()).hexdigest()
        == published.artifact.payload.source_sha256
    )

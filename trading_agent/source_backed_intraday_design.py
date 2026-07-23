from __future__ import annotations

from dataclasses import dataclass
from typing import override

from pydantic import ValidationError

from trading_agent.experiment_ledger_models import (
    ResearchSource,
    ResearchSourceKind,
    StrategyVersionRegistration,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerReader,
    ExperimentLedgerStore,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.intraday_research_loop_models import IntradayResearchManifest
from trading_agent.source_backed_intraday_design_validation import (
    validated_source_backed_registration,
)
from trading_agent.source_driven_hypothesis_queue import (
    project_source_driven_hypothesis_queue,
)
from trading_agent.source_driven_hypothesis_queue_models import (
    InvalidSourceDrivenHypothesisQueueError,
    SourceDrivenHypothesisQueueArtifact,
)


class InvalidSourceBackedIntradayDesignError(ValueError):
    @override
    def __str__(self) -> str:
        return "source-backed intraday strategy design is invalid"


@dataclass(frozen=True, slots=True)
class SourceBackedIntradayDesignResult:
    versions_created: int
    versions_total: int


def register_source_backed_intraday_design(
    manifest: IntradayResearchManifest,
    queue: SourceDrivenHypothesisQueueArtifact,
    ledger: ExperimentLedgerStore,
) -> SourceBackedIntradayDesignResult:
    try:
        checked_queue = SourceDrivenHypothesisQueueArtifact.model_validate(queue.model_dump(mode="python"))
        if (
            manifest.schema_version != 2
            or manifest.family != "source_backed_intraday_challengers_v2"
            or manifest.source_queue_snapshot_id != checked_queue.snapshot_id
            or manifest.registered_at < checked_queue.snapshot.as_of
        ):
            raise InvalidSourceBackedIntradayDesignError

        reader = ExperimentLedgerReader(ledger.path)
        current_queue = project_source_driven_hypothesis_queue(reader)
        queue_is_current = current_queue.snapshot_id == checked_queue.snapshot_id
        cards = reader.research_hypothesis_cards()
        sources = {str(stored.source_key): stored.source for stored in reader.research_sources()}
        registrations: list[StrategyVersionRegistration] = []
        for selection in manifest.hypotheses:
            card_key = selection.queue_card_key
            if card_key is None:
                raise InvalidSourceBackedIntradayDesignError
            queue_items = tuple(item for item in checked_queue.snapshot.items if item.card_key == card_key)
            stored_cards = tuple(stored for stored in cards if str(stored.card_key) == card_key)
            if len(queue_items) != 1 or len(stored_cards) != 1:
                raise InvalidSourceBackedIntradayDesignError
            item = queue_items[0]
            card = stored_cards[0].card
            source_kinds = _source_kinds(card.research_source_keys, sources)
            registration = validated_source_backed_registration(
                reader,
                item,
                selection,
                card,
                source_kinds,
                manifest,
                queue_is_current=queue_is_current,
            )
            registrations.append(registration)

        created = 0
        with ledger.writer() as writer:
            for registration in registrations:
                created += writer.register_strategy_version(registration)
        return SourceBackedIntradayDesignResult(
            versions_created=created,
            versions_total=len(registrations),
        )
    except InvalidSourceBackedIntradayDesignError:
        raise
    except (
        ExperimentLedgerConflictError,
        InvalidExperimentLedgerSourceError,
        InvalidSourceDrivenHypothesisQueueError,
        KeyError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidSourceBackedIntradayDesignError from None


def _source_kinds(
    source_keys: tuple[str, ...],
    sources: dict[str, ResearchSource],
) -> tuple[ResearchSourceKind, ...]:
    kinds: list[ResearchSourceKind] = []
    for key in source_keys:
        source = sources[key]
        kinds.append(source.source_kind)
    return tuple(sorted(set(kinds), key=str))


__all__ = (
    "InvalidSourceBackedIntradayDesignError",
    "SourceBackedIntradayDesignResult",
    "register_source_backed_intraday_design",
)

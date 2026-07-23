from __future__ import annotations

from dataclasses import dataclass
from typing import override

from pydantic import ValidationError

from trading_agent.daily_research_contract import (
    CURRENT_COST_MODEL,
    CURRENT_DATA_CONTRACT,
    SHADOW_PORTFOLIO_POLICY,
    strategy_contract,
)
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
from trading_agent.lane_identity_models import LaneId
from trading_agent.source_driven_hypothesis_queue_models import (
    HypothesisQueueRoute,
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
        cards = reader.research_hypothesis_cards()
        sources = {str(stored.source_key): stored.source for stored in reader.research_sources()}
        existing_versions = reader.strategy_versions()
        registrations: list[StrategyVersionRegistration] = []
        for selection in manifest.hypotheses:
            strategy_version = selection.strategy_version
            card_key = selection.queue_card_key
            if strategy_version is None or card_key is None:
                raise InvalidSourceBackedIntradayDesignError
            queue_items = tuple(item for item in checked_queue.snapshot.items if item.card_key == card_key)
            stored_cards = tuple(stored for stored in cards if str(stored.card_key) == card_key)
            if len(queue_items) != 1 or len(stored_cards) != 1:
                raise InvalidSourceBackedIntradayDesignError
            item = queue_items[0]
            card = stored_cards[0].card
            source_kinds = _source_kinds(card.research_source_keys, sources)
            if (
                item.route is not HypothesisQueueRoute.STRATEGY_DESIGN
                or item.hypothesis_id != selection.hypothesis_id
                or item.lane_id is not LaneId.INTRADAY_MOMENTUM
                or item.registered_at != card.hypothesis.ledger_recorded_at
                or item.hypothesis != card.hypothesis.hypothesis
                or item.falsification_rule != card.hypothesis.falsification_rule
                or item.economic_mechanism != card.economic_mechanism
                or item.counterfactual_baseline != card.counterfactual_baseline
                or item.source_keys != card.research_source_keys
                or item.source_kinds != source_kinds
                or item.strategy_versions
                or item.historical_trial_ids
                or card.hypothesis.hypothesis_id != selection.hypothesis_id
                or card.hypothesis.primary_lane is not LaneId.INTRADAY_MOMENTUM
                or manifest.registered_at < card.hypothesis.ledger_recorded_at
            ):
                raise InvalidSourceBackedIntradayDesignError
            template = strategy_contract(selection.strategy)
            registration = StrategyVersionRegistration(
                strategy_id=selection.strategy.value,
                strategy_version=strategy_version,
                hypothesis_id=card.hypothesis.hypothesis_id,
                experiment_scope_key=card.hypothesis.experiment_scope_key,
                lane_id=card.hypothesis.primary_lane,
                code_version=manifest.code_version,
                parameter_set=template.parameter_set,
                data_contract=CURRENT_DATA_CONTRACT,
                cost_model=CURRENT_COST_MODEL,
                portfolio_policy=SHADOW_PORTFOLIO_POLICY,
                source_registered_at=card.hypothesis.source_registered_at,
                ledger_recorded_at=manifest.registered_at,
            )
            prior = tuple(
                stored.registration
                for stored in existing_versions
                if stored.registration.hypothesis_id == selection.hypothesis_id
            )
            if prior and (len(prior) != 1 or prior[0] != registration):
                raise InvalidSourceBackedIntradayDesignError
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

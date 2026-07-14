from __future__ import annotations

from pathlib import Path

import httpx2

from tests.paper_entry_mutation_fixtures import FakeEntryMutationBroker, entry_order
from tests.trade_update_ledger_fixtures import FINGERPRINT, OBSERVED_AT, initialized_store, intent
from trading_agent.paper_mutation_executor import (
    PaperMutationExecutor,
    PaperMutationExecutorDependencies,
)
from trading_agent.paper_mutation_executor_models import PaperMutationExecutionState


def test_entry_intent_is_durable_before_post_and_ack_is_idempotent(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    broker = FakeEntryMutationBroker(store.path)

    with store.writer() as writer:
        executor = PaperMutationExecutor(
            PaperMutationExecutorDependencies(
                writer,
                store.paper_mutation_events,
                broker,
                lambda: OBSERVED_AT,
            )
        )
        first = executor.execute_entry(FINGERPRINT, entry_order())
        replay = executor.execute_entry(FINGERPRINT, entry_order())

    assert first.state is PaperMutationExecutionState.ACKNOWLEDGED
    assert replay.state is PaperMutationExecutionState.ALREADY_ACKNOWLEDGED
    assert broker.calls == ["entry:AAA"]
    assert store.intents()[0].intent_id == intent().intent_id
    assert store.paper_mutation_intents()[0].intent.entry_intent_id == intent().intent_id


def test_entry_timeout_is_ambiguous_and_does_not_repost(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    broker = FakeEntryMutationBroker(store.path)
    broker.entry_failure = httpx2.ReadTimeout("timeout")

    with store.writer() as writer:
        executor = PaperMutationExecutor(
            PaperMutationExecutorDependencies(
                writer,
                store.paper_mutation_events,
                broker,
                lambda: OBSERVED_AT,
            )
        )
        first = executor.execute_entry(FINGERPRINT, entry_order())
        replay = executor.execute_entry(FINGERPRINT, entry_order())

    assert first.state is PaperMutationExecutionState.AMBIGUOUS
    assert replay.state is PaperMutationExecutionState.AMBIGUOUS
    assert broker.calls == ["entry:AAA"]

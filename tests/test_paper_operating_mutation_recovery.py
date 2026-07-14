from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import httpx2
import pytest

from tests.paper_runtime_fixtures import market_clock
from tests.paper_trade_update_ingestion_fixtures import (
    TradeUpdateStream,
    broker_state,
    recovery_state,
)
from tests.test_paper_mutation_executor import FakeMutationBroker, _protective_plan
from tests.test_paper_mutation_recovery import _oco
from tests.trade_update_ledger_fixtures import FINGERPRINT, OBSERVED_AT, initialized_store
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.paper_mutation_executor import (
    PaperMutationExecutor,
    PaperMutationExecutorDependencies,
)
from trading_agent.paper_mutation_recovery_models import PaperMutationRecoveryState
from trading_agent.paper_operating_session_models import PaperOperatingSession
from trading_agent.paper_stream_owner import PaperStreamOwnerDependencies
from trading_agent.paper_stream_recovery import PaperRecoveryState
from trading_agent.paper_stream_recovery_models import (
    PaperProtectiveOcoMutationLookup,
)
from trading_agent.paper_stream_recovery_runtime import (
    PaperStreamRecoveryIncompleteError,
)
from trading_agent.paper_trade_update_runtime import (
    PaperOperatingSessionDependencies,
    _open_paper_operating_session,
)


def test_operating_session_surface_exposes_current_epoch_mutation_recovery() -> None:
    assert "recover_mutations" in PaperOperatingSession.__dict__


def test_operating_session_recovers_ambiguous_oco_inside_current_stream_epoch(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    attempted_at = OBSERVED_AT - dt.timedelta(seconds=10)
    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_protective_plan(), attempted_at)
    broker = FakeMutationBroker(store.path)
    broker.oco_failure = httpx2.ReadTimeout("timeout")
    with store.writer() as writer:
        result = PaperMutationExecutor(
            PaperMutationExecutorDependencies(
                writer,
                store.paper_mutation_events,
                broker,
                lambda: attempted_at,
            )
        ).execute_protective_oco(FINGERPRINT, store.protective_oco_plans()[0])
    assert result.state.value == "ambiguous"

    stream = TradeUpdateStream()

    @contextmanager
    def stream_opener(
        _: AlpacaPaperCredentials,
    ) -> Iterator[TradeUpdateStream]:
        yield stream

    def recovery_loader(
        _: AlpacaPaperCredentials,
        ledger: ReconciliationLedger,
    ) -> PaperRecoveryState:
        observed_at = OBSERVED_AT + dt.timedelta(seconds=stream.heartbeat_count - 1.5)
        protection = _oco(observed_at)
        base = recovery_state(ledger.unresolved_intent_ids, observed_at)
        return replace(
            base,
            broker_state=replace(
                base.broker_state,
                protective_ocos=(protection,),
            ),
            protective_ocos=(protection,),
            mutation_lookups=(
                PaperProtectiveOcoMutationLookup(
                    ledger.paper_mutation_intents[0].mutation_key,
                    observed_at,
                    protection,
                ),
            ),
        )

    dependencies = PaperOperatingSessionDependencies(
        PaperStreamOwnerDependencies(
            recovery_loader,
            stream_opener,
            lambda: OBSERVED_AT + dt.timedelta(seconds=4),
        ),
        lambda _: (broker_state(OBSERVED_AT), market_clock()),
        lambda: OBSERVED_AT + dt.timedelta(seconds=4),
    )
    with _open_paper_operating_session(
        AlpacaPaperCredentials("test-key", "test-secret"),
        store,
        dependencies,
    ) as session:
        recovered = session.recover_mutations()

    assert recovered[0].state is PaperMutationRecoveryState.ACKNOWLEDGED
    assert stream.heartbeat_count == 4
    assert store.paper_mutation_events()[-1].event.event_type.value == "recovered_acknowledged"
    assert '"mutation_lookups"' in store.paper_stream_recoveries()[-1].snapshot_json


def test_operating_session_rejects_generic_inventory_for_ambiguous_oco(
    tmp_path: Path,
) -> None:
    # Given: an ambiguous OCO and a loader that omits deterministic lookup evidence.
    store = initialized_store(tmp_path)
    attempted_at = OBSERVED_AT - dt.timedelta(seconds=10)
    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_protective_plan(), attempted_at)
    broker = FakeMutationBroker(store.path)
    broker.oco_failure = httpx2.ReadTimeout("timeout")
    with store.writer() as writer:
        _ = PaperMutationExecutor(
            PaperMutationExecutorDependencies(
                writer,
                store.paper_mutation_events,
                broker,
                lambda: attempted_at,
            )
        ).execute_protective_oco(FINGERPRINT, store.protective_oco_plans()[0])
    stream = TradeUpdateStream()

    @contextmanager
    def stream_opener(
        _: AlpacaPaperCredentials,
    ) -> Iterator[TradeUpdateStream]:
        yield stream

    def generic_loader(
        _: AlpacaPaperCredentials,
        ledger: ReconciliationLedger,
    ) -> PaperRecoveryState:
        observed_at = OBSERVED_AT + dt.timedelta(seconds=stream.heartbeat_count - 1.5)
        protection = _oco(observed_at)
        base = recovery_state(ledger.unresolved_intent_ids, observed_at)
        return replace(
            base,
            broker_state=replace(base.broker_state, protective_ocos=(protection,)),
            protective_ocos=(protection,),
        )

    dependencies = PaperOperatingSessionDependencies(
        PaperStreamOwnerDependencies(
            generic_loader,
            stream_opener,
            lambda: OBSERVED_AT + dt.timedelta(seconds=4),
        ),
        lambda _: (broker_state(OBSERVED_AT), market_clock()),
        lambda: OBSERVED_AT + dt.timedelta(seconds=4),
    )

    # When / Then: the current-epoch barrier rejects generic-only evidence.
    with (
        pytest.raises(PaperStreamRecoveryIncompleteError, match="targeted"),
        _open_paper_operating_session(
            AlpacaPaperCredentials("test-key", "test-secret"),
            store,
            dependencies,
        ),
    ):
        pass

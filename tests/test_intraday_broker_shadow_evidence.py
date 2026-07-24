from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from tests.intraday_broker_shadow_fixtures import (
    EXIT_AT,
    REVIEWED_AT,
    entry_state,
    exit_activity,
    intent,
    protective_exit,
    shadow_trade,
)
from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.intraday_broker_shadow_evidence import (
    build_broker_shadow_evidence,
)
from trading_agent.intraday_broker_shadow_models import (
    BrokerShadowEvidenceRequest,
)
from trading_agent.metrics import net_return
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerOrderId,
    PaperOrderSide,
)
from trading_agent.paper_mutation_keys import PaperMutationEventKey, PaperMutationKey
from trading_agent.paper_mutation_ledger_models import (
    PaperMutationEvent,
    PaperMutationEventType,
    PaperMutationIntent,
    PaperMutationOperation,
)
from trading_agent.paper_mutation_store import (
    StoredPaperMutationEvent,
    StoredPaperMutationIntent,
)


def test_evidence_collects_without_claiming_readiness_before_paper_pairs() -> None:
    # Given: an exact but empty Paper execution snapshot and no paired shadow trades.
    request = BrokerShadowEvidenceRequest(
        strategy_version="orb-v1",
        execution_snapshot_sha256="a" * 64,
        shadow_source_sha256="b" * 64,
        shadow_trades=(),
        ledger=ReconciliationLedger((), frozenset(), None),
        account_activities=(),
        protective_oco_snapshots=(),
        reviewed_at=REVIEWED_AT,
    )

    # When: the query-only promotion diagnostic is calculated.
    evidence = build_broker_shadow_evidence(request)

    # Then: the absent live Paper sample remains explicit and cannot become ready.
    assert evidence.status.value == "collecting"
    assert evidence.paired_trade_count == 0
    assert evidence.paired_session_count == 0
    assert evidence.blockers == (
        "minimum_paired_sessions:0/60",
        "minimum_paired_trades:0/100",
    )
    assert evidence.automatic_state_change_allowed is False
    assert evidence.order_authority_change_allowed is False
    assert evidence.allocation_change_allowed is False


def test_evidence_pairs_exact_broker_exit_with_conservative_shadow_trade() -> None:
    # Given: one completed Paper entry and its acknowledged protective OCO fill.
    shadow = shadow_trade()
    stored_intent = intent()
    state = entry_state()
    stored_plan, stored_snapshot = protective_exit()
    request = BrokerShadowEvidenceRequest(
        strategy_version=stored_intent.strategy_version,
        execution_snapshot_sha256="a" * 64,
        shadow_source_sha256="b" * 64,
        shadow_trades=(shadow,),
        ledger=ReconciliationLedger(
            intents=(stored_intent,),
            unresolved_intent_ids=frozenset(),
            account_fingerprint=AccountFingerprint("account"),
            filled_intent_ids=frozenset((stored_intent.intent_id,)),
            order_states=(state,),
            protective_oco_plans=(stored_plan,),
        ),
        account_activities=(exit_activity(),),
        protective_oco_snapshots=(stored_snapshot,),
        reviewed_at=REVIEWED_AT,
    )

    # When: the same recommendation identity is paired across both ledgers.
    evidence = build_broker_shadow_evidence(request)

    # Then: both costed returns and their implementation difference are immutable.
    pair = evidence.pairs[0]
    expected_broker = 11.9 * 0.998 / (10.1 * 1.002) - 1.0
    assert evidence.paired_trade_count == 1
    assert evidence.paired_session_count == 1
    assert pair.recommendation_id == shadow.recommendation_id
    assert pair.broker_entry == pytest.approx(10.1)
    assert pair.broker_exit == pytest.approx(11.9)
    assert pair.broker_net_return == pytest.approx(expected_broker)
    assert pair.shadow_net_return == pytest.approx(net_return(shadow, 20))
    assert pair.return_difference == pytest.approx(pair.broker_net_return - pair.shadow_net_return)


def test_evidence_pairs_acknowledged_eod_close_position_fill() -> None:
    # Given: an entry exits through the recorded EOD close-position mutation.
    shadow = shadow_trade()
    stored_intent = intent()
    mutation_key = PaperMutationKey("m" * 64)
    mutation = StoredPaperMutationIntent(
        mutation_key,
        PaperMutationIntent(
            AccountFingerprint("account"),
            EXIT_AT,
            PaperMutationOperation.CLOSE_POSITION,
            None,
            "safety-plan",
            1,
            "c" * 64,
            "FAST",
            None,
            PaperOrderSide.SELL,
            Decimal(2),
        ),
    )
    event = StoredPaperMutationEvent(
        1,
        PaperMutationEventKey("e" * 64),
        mutation_key,
        PaperMutationEvent(
            1,
            EXIT_AT,
            PaperMutationEventType.ACKNOWLEDGED,
            "request-1",
            200,
            BrokerOrderId("close-order"),
            "d" * 64,
        ),
    )
    close_activity = replace(
        exit_activity(),
        activity=replace(
            exit_activity().activity,
            broker_order_id=BrokerOrderId("close-order"),
        ),
    )
    request = BrokerShadowEvidenceRequest(
        strategy_version=stored_intent.strategy_version,
        execution_snapshot_sha256="a" * 64,
        shadow_source_sha256="b" * 64,
        shadow_trades=(shadow,),
        ledger=ReconciliationLedger(
            intents=(stored_intent,),
            unresolved_intent_ids=frozenset(),
            account_fingerprint=AccountFingerprint("account"),
            filled_intent_ids=frozenset((stored_intent.intent_id,)),
            order_states=(entry_state(),),
            paper_mutation_intents=(mutation,),
            paper_mutation_events=(event,),
        ),
        account_activities=(close_activity,),
        protective_oco_snapshots=(),
        reviewed_at=REVIEWED_AT,
    )

    # When: the query-only pairing follows the acknowledged close order ID.
    evidence = build_broker_shadow_evidence(request)

    # Then: the EOD flat fill is paired instead of being discarded as external.
    assert evidence.paired_trade_count == 1
    assert evidence.unpaired_broker_intent_count == 0

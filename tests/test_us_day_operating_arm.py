from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_hermes_arm_gateway import (
    OWNER,
    SCOPE,
    STRATEGY,
    DeterministicNonce,
    ManualClock,
    MutableAuthorityResolver,
)
from trading_agent.hermes_arm_gateway import HermesArmGateway, HermesArmGatewayConfig
from trading_agent.hermes_arm_request import (
    HermesArmConfirmCommand,
    HermesArmConsumeCommand,
    HermesArmFailure,
    HermesArmPrepareCommand,
    HermesArmTransitionKind,
    InvalidHermesArmRequestError,
)
from trading_agent.hermes_arm_signing import HermesArmSigner
from trading_agent.hermes_arm_store import HermesArmStore
from trading_agent.paper_mutation_arm import PAPER_MUTATION_ARM_VALUE
from trading_agent.us_day_operating_arm import StrategyBoundHermesArmConsumer


def test_strategy_bound_arm_rejects_mismatch_without_consuming_request(tmp_path: Path) -> None:
    signer = HermesArmSigner.from_bytes(b"x" * 32)
    store = HermesArmStore(tmp_path / "arm.sqlite3", signer)
    gateway = HermesArmGateway(
        HermesArmGatewayConfig(
            store=store,
            authority_resolver=MutableAuthorityResolver(),
            signer=signer,
            clock=ManualClock(),
            nonce_factory=DeterministicNonce(),
            ttl_seconds=300,
        )
    )
    prepared = gateway.prepare(HermesArmPrepareCommand(owner_id_hash=OWNER, scope=SCOPE))
    confirmed = gateway.confirm(
        HermesArmConfirmCommand(
            owner_id_hash=OWNER,
            request_id=prepared.request_id,
            confirmation=prepared.confirmation,
        )
    )
    command = HermesArmConsumeCommand(request_id=confirmed.request_id, expected_scope=SCOPE)
    consumer = StrategyBoundHermesArmConsumer(gateway, store)

    with pytest.raises(InvalidHermesArmRequestError) as mismatch:
        _ = consumer.consume(command, "wrong-strategy")

    assert mismatch.value.reason is HermesArmFailure.CHAMPION_MISMATCH
    assert gateway.status(confirmed.request_id).status is HermesArmTransitionKind.CONFIRMED
    assert consumer.consume(command, STRATEGY).value == PAPER_MUTATION_ARM_VALUE
    assert gateway.status(confirmed.request_id).status is HermesArmTransitionKind.CONSUMED

from __future__ import annotations

import datetime as dt
import secrets

from trading_agent.alpaca_paper_config import load_alpaca_paper_credentials
from trading_agent.execution_store import ExecutionStore
from trading_agent.hermes_arm_authority import LedgerHermesArmAuthorityConfig, LedgerHermesArmAuthorityResolver
from trading_agent.hermes_arm_gateway import HermesArmGateway, HermesArmGatewayConfig
from trading_agent.hermes_arm_signing import HermesArmSigner, load_hermes_arm_signing_key
from trading_agent.hermes_arm_store import HermesArmStore
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.us_day_operating_arm import StrategyBoundHermesArmConsumer
from trading_agent.us_day_operating_cli_contract import RunUsDayCommand
from trading_agent.us_day_operating_cli_errors import UninitializedUsDayExecutionStoreError
from trading_agent.us_day_operating_coordinator import UsDayOperatingCoordinator, UsDayOperatingCoordinatorConfig


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def build_runner(command: RunUsDayCommand) -> UsDayOperatingCoordinator:
    execution_store = ExecutionStore(command.stores.execution)
    if not execution_store.is_initialized():
        raise UninitializedUsDayExecutionStoreError
    signer = HermesArmSigner(load_hermes_arm_signing_key(command.authority.signing_key))
    arm_store = HermesArmStore(command.stores.arm, signer)
    resolver = LedgerHermesArmAuthorityResolver(
        LedgerHermesArmAuthorityConfig(
            repository=command.authority.repository,
            lane_registry=command.authority.lane_registry,
            experiment_ledger=command.authority.experiment_ledger,
        )
    )
    gateway = HermesArmGateway(
        HermesArmGatewayConfig(
            store=arm_store,
            authority_resolver=resolver,
            signer=signer,
            clock=now_utc,
            nonce_factory=lambda: secrets.token_bytes(32),
            ttl_seconds=300,
        )
    )
    return UsDayOperatingCoordinator(
        UsDayOperatingCoordinatorConfig(
            arm_consumer=StrategyBoundHermesArmConsumer(gateway, arm_store),
            credentials=load_alpaca_paper_credentials(),
            execution_store=execution_store,
            delivery_store=HermesDeliveryStore(command.stores.delivery),
        )
    )

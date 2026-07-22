from __future__ import annotations

import hmac
from dataclasses import dataclass

from trading_agent.hermes_arm_gateway import HermesArmGateway
from trading_agent.hermes_arm_request import (
    HermesArmConsumeCommand,
    HermesArmFailure,
    InvalidHermesArmRequestError,
)
from trading_agent.hermes_arm_store import HermesArmStore
from trading_agent.paper_mutation_arm import PaperMutationArm


@dataclass(frozen=True, slots=True)
class StrategyBoundHermesArmConsumer:
    gateway: HermesArmGateway
    store: HermesArmStore

    def consume(self, command: HermesArmConsumeCommand, expected_strategy_version: str) -> PaperMutationArm:
        request = self.store.request(command.request_id)
        if not hmac.compare_digest(request.authority.strategy_version, expected_strategy_version):
            raise InvalidHermesArmRequestError(HermesArmFailure.CHAMPION_MISMATCH)
        return self.gateway.consume(command)

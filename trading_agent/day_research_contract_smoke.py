from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.day_research_contract_foundation import (
    SyntheticContractFoundation,
    build_synthetic_contract_foundation,
)
from trading_agent.day_research_contract_market import (
    SyntheticMarketContract,
    build_synthetic_market_contract,
)
from trading_agent.day_strategy_capsule import publish_day_strategy_capsule
from trading_agent.day_strategy_capsule_models import StrategyCapsule
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.research_identity_models import MarketId


class InvalidDayResearchContractSmokeError(ValueError):
    pass


class ContractSmokeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class ContractMarketPair(ContractSmokeModel):
    version_market_id: MarketId
    capsule_market_id: MarketId

    @model_validator(mode="after")
    def validate_pair(self) -> Self:
        if self.version_market_id is not self.capsule_market_id:
            raise InvalidDayResearchContractSmokeError("cross_market_contract_fixture")
        return self


class DayResearchContractFixture(ContractSmokeModel):
    schema_version: Literal[1]
    fixture_kind: Literal["synthetic_contract_only"]
    markets: tuple[ContractMarketPair, ContractMarketPair]

    @model_validator(mode="after")
    def validate_markets(self) -> Self:
        version_markets = tuple(item.version_market_id for item in self.markets)
        expected = tuple(sorted(MarketId, key=lambda item: item.value))
        if version_markets != expected:
            raise InvalidDayResearchContractSmokeError("dual_market_contract_fixture_required")
        return self


class ContractMarketResult(ContractSmokeModel):
    capsule_id: str
    hypothesis_version_id: str
    market_id: MarketId
    status: Literal["research_only"]


class ContractSmokeResult(ContractSmokeModel):
    family_id: str
    markets: tuple[ContractMarketResult, ContractMarketResult]
    status: Literal["contract_only"] = "contract_only"
    synthetic_fixture: Literal[True] = True


def run_day_research_contract_smoke(
    fixture_path: Path,
    database: Path,
) -> ContractSmokeResult:
    fixture = _load_fixture(fixture_path)
    foundation = build_synthetic_contract_foundation()
    contracts = tuple(
        build_synthetic_market_contract(
            foundation.family,
            pair.version_market_id,
            branch_index,
        )
        for branch_index, pair in enumerate(fixture.markets)
    )
    store = ExperimentLedgerStore(database.expanduser().resolve(strict=False))
    _register_contracts(store, foundation, contracts)
    capsules = tuple(publish_day_strategy_capsule(store, contract.capsule_request)[0] for contract in contracts)
    return _result(foundation.family.family_id, contracts, capsules)


def _load_fixture(path: Path) -> DayResearchContractFixture:
    payload = path.expanduser().read_bytes()
    if not payload or len(payload) > 64 * 1024:
        raise InvalidDayResearchContractSmokeError("contract_fixture_size_invalid")
    return DayResearchContractFixture.model_validate_json(payload)


def _register_contracts(
    store: ExperimentLedgerStore,
    foundation: SyntheticContractFoundation,
    contracts: tuple[SyntheticMarketContract, ...],
) -> None:
    with store.writer() as writer:
        _ = writer.register_strategy_research(foundation.manifest)
        _ = writer.register_day_hypothesis_family(foundation.family)
        for contract in contracts:
            _ = writer.register_day_hypothesis_version(contract.version)
            _ = writer.append_strategy_research_attempt(contract.attempt)
            _ = writer.register_day_research_attempt_binding(contract.binding)


def _result(
    family_id: str,
    contracts: tuple[SyntheticMarketContract, ...],
    capsules: tuple[StrategyCapsule, ...],
) -> ContractSmokeResult:
    capsule_by_market = {capsule.market_id: capsule for capsule in capsules}
    markets = tuple(
        ContractMarketResult(
            capsule_id=capsule_by_market[contract.version.market_id].capsule_id,
            hypothesis_version_id=contract.version.hypothesis_version_id,
            market_id=contract.version.market_id,
            status="research_only",
        )
        for contract in contracts
    )
    return ContractSmokeResult(
        family_id=family_id,
        markets=(markets[0], markets[1]),
    )


__all__ = (
    "ContractMarketPair",
    "ContractMarketResult",
    "ContractSmokeResult",
    "DayResearchContractFixture",
    "InvalidDayResearchContractSmokeError",
    "run_day_research_contract_smoke",
)

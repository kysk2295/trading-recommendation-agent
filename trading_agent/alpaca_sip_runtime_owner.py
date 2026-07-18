from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import final

from trading_agent.alpaca_sip_runtime_adapter import AlpacaSipRuntimeAdapter
from trading_agent.alpaca_sip_runtime_evidence import AlpacaSipRuntimeEvidenceProjector
from trading_agent.alpaca_sip_runtime_evidence_store import AlpacaSipRuntimeEvidenceStore
from trading_agent.alpaca_sip_runtime_http import AlpacaSipMinutePageClient
from trading_agent.alpaca_sip_runtime_models import (
    AlpacaSipRuntimeContext,
    AlpacaSipRuntimeError,
)
from trading_agent.us_market_data_fleet import RuntimeOwnerBlockedError
from trading_agent.us_market_data_runtime_models import (
    MarketDataRuntimeError,
    MarketDataRuntimeResult,
    RuntimeFeatureRequest,
)
from trading_agent.us_market_data_runtime_store import (
    MarketDataRuntimeStore,
    MarketDataWriterLeaseUnavailableError,
    UnsupportedMarketDataRuntimeSchemaError,
)
from trading_agent.us_market_data_supervisor import UsMarketDataSupervisor
from trading_agent.us_subscription_models import (
    DesiredMarketDataSubscription,
    SubscriptionPolicyDecision,
)


@dataclass(frozen=True, slots=True)
class AlpacaSipRuntimeOwnerFactoryConfig:
    runtime_root: Path
    canonical_root: Path
    session_date: dt.date
    clock: Callable[[], dt.datetime]


@final
class AlpacaSipRuntimeOwner:
    __slots__ = ("_subscription", "_supervisor")

    def __init__(
        self,
        subscription: DesiredMarketDataSubscription,
        supervisor: UsMarketDataSupervisor,
    ) -> None:
        self._subscription = subscription
        self._supervisor = supervisor

    @property
    def instrument_id(self) -> str:
        return self._subscription.instrument_id

    @property
    def symbol(self) -> str:
        return self._subscription.symbol

    def run_cycle(
        self,
        decision: SubscriptionPolicyDecision,
        request: RuntimeFeatureRequest,
    ) -> MarketDataRuntimeResult:
        if (
            type(decision) is not SubscriptionPolicyDecision
            or type(request) is not RuntimeFeatureRequest
            or request.instrument_id != self.instrument_id
            or self._subscription not in decision.desired
        ):
            raise RuntimeOwnerBlockedError
        narrowed = replace(
            decision,
            desired=(self._subscription,),
            actions=(),
            new_cooldowns=(),
        )
        try:
            return self._supervisor.run_cycle(narrowed, (request,))
        except (
            AlpacaSipRuntimeError,
            MarketDataRuntimeError,
            MarketDataWriterLeaseUnavailableError,
            UnsupportedMarketDataRuntimeSchemaError,
            OSError,
        ):
            raise RuntimeOwnerBlockedError from None


@final
class AlpacaSipRuntimeOwnerFactory:
    __slots__ = ("_config", "_page_client")

    def __init__(
        self,
        page_client: AlpacaSipMinutePageClient,
        config: AlpacaSipRuntimeOwnerFactoryConfig,
    ) -> None:
        if (
            type(page_client) is not AlpacaSipMinutePageClient
            or type(config) is not AlpacaSipRuntimeOwnerFactoryConfig
            or type(config.session_date) is not dt.date
            or not callable(config.clock)
        ):
            raise RuntimeOwnerBlockedError
        self._page_client = page_client
        self._config = config

    def create(
        self,
        subscription: DesiredMarketDataSubscription,
    ) -> AlpacaSipRuntimeOwner:
        if type(subscription) is not DesiredMarketDataSubscription:
            raise RuntimeOwnerBlockedError
        owner_key = _owner_key(subscription)
        owner_dir = _private_directory(_private_directory(self._config.runtime_root) / owner_key)
        canonical_root = _private_directory(self._config.canonical_root)
        evidence = AlpacaSipRuntimeEvidenceStore(owner_dir / "evidence.sqlite3")
        projector = AlpacaSipRuntimeEvidenceProjector(
            evidence,
            canonical_root / owner_key,
        )
        adapter = AlpacaSipRuntimeAdapter(
            self._page_client,
            projector,
            AlpacaSipRuntimeContext(
                self._config.session_date,
                subscription.instrument_id,
                subscription.symbol,
                self._config.clock,
            ),
        )
        supervisor = UsMarketDataSupervisor(
            adapter,
            MarketDataRuntimeStore(owner_dir / "runtime.sqlite3"),
            clock=self._config.clock,
        )
        return AlpacaSipRuntimeOwner(subscription, supervisor)


def _owner_key(subscription: DesiredMarketDataSubscription) -> str:
    encoded = json.dumps(
        {
            "instrument_id": subscription.instrument_id,
            "symbol": subscription.symbol,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _private_directory(path: Path) -> Path:
    candidate = path.expanduser().absolute()
    try:
        if candidate.is_symlink():
            raise RuntimeOwnerBlockedError
        candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = candidate.lstat()
    except OSError:
        raise RuntimeOwnerBlockedError from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise RuntimeOwnerBlockedError
    return candidate


__all__ = (
    "AlpacaSipRuntimeOwner",
    "AlpacaSipRuntimeOwnerFactory",
    "AlpacaSipRuntimeOwnerFactoryConfig",
)

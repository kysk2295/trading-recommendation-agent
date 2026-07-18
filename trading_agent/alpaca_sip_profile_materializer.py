from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import final, override

from trading_agent.alpaca_sip_historical_profile import (
    AlpacaSipHistoricalProfileCollector,
    AlpacaSipHistoricalProfileError,
)
from trading_agent.alpaca_sip_runtime_evidence import AlpacaSipRuntimeEvidenceProjector
from trading_agent.alpaca_sip_runtime_evidence_store import AlpacaSipRuntimeEvidenceStore
from trading_agent.alpaca_sip_runtime_http import AlpacaSipMinutePageClient
from trading_agent.us_equity_calendar import NEW_YORK
from trading_agent.us_intraday_volume_profile_artifact import (
    IntradayVolumeProfileArtifactError,
    IntradayVolumeProfileArtifactStore,
)
from trading_agent.us_runtime_fleet_cycle import ProfileArtifactBinding
from trading_agent.us_runtime_policy_scope import PreparedRuntimePolicyScope
from trading_agent.us_subscription_models import DesiredMarketDataSubscription


class AlpacaSipProfileMaterializerError(ValueError):
    @override
    def __str__(self) -> str:
        return "alpaca SIP profile materialization is blocked"


@final
class AlpacaSipProfileMaterializer:
    __slots__ = ("_page_client", "_root")

    def __init__(self, page_client: AlpacaSipMinutePageClient, root: Path) -> None:
        if type(page_client) is not AlpacaSipMinutePageClient:
            raise AlpacaSipProfileMaterializerError
        self._page_client = page_client
        self._root = _private_directory(root)

    def materialize(
        self,
        scope: PreparedRuntimePolicyScope,
    ) -> tuple[ProfileArtifactBinding, ...]:
        try:
            if type(scope) is not PreparedRuntimePolicyScope:
                raise AlpacaSipProfileMaterializerError
            target = scope.decision.evaluated_at.astimezone(NEW_YORK).date()
            return tuple(self._binding(item, target, scope.completed_minute) for item in scope.decision.desired)
        except (
            AlpacaSipHistoricalProfileError,
            AttributeError,
            IntradayVolumeProfileArtifactError,
            OSError,
            TypeError,
            ValueError,
        ):
            raise AlpacaSipProfileMaterializerError from None

    def _binding(
        self,
        subscription: DesiredMarketDataSubscription,
        target: dt.date,
        through_minute: int,
    ) -> ProfileArtifactBinding:
        if type(subscription) is not DesiredMarketDataSubscription:
            raise AlpacaSipProfileMaterializerError
        owner = _private_directory(self._root / _owner_key(subscription))
        evidence = AlpacaSipRuntimeEvidenceStore(owner / "evidence.sqlite3")
        projector = AlpacaSipRuntimeEvidenceProjector(evidence, owner / "canonical")
        collector = AlpacaSipHistoricalProfileCollector(self._page_client, evidence, projector)
        profile = collector.collect(
            subscription.instrument_id,
            subscription.symbol,
            target,
            through_minute=through_minute,
        )
        path = IntradayVolumeProfileArtifactStore(owner / "artifacts").append(profile)
        return ProfileArtifactBinding(subscription.instrument_id, path)


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
            raise AlpacaSipProfileMaterializerError
        candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = candidate.lstat()
    except OSError:
        raise AlpacaSipProfileMaterializerError from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise AlpacaSipProfileMaterializerError
    return candidate


__all__ = (
    "AlpacaSipProfileMaterializer",
    "AlpacaSipProfileMaterializerError",
)

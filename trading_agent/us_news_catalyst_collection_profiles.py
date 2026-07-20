from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.alpaca_sip_profile_materializer import load_materialized_profile
from trading_agent.us_intraday_volume_profile_artifact import (
    IntradayVolumeProfileArtifactStore,
)
from trading_agent.us_intraday_volume_profile_models import IntradayVolumeProfileEvidence
from trading_agent.us_news_catalyst_collection_models import UsNewsCatalystCollectionPlan
from trading_agent.us_runtime_fleet_cycle import ProfileArtifactBinding
from trading_agent.us_subscription_models import DesiredMarketDataSubscription


class InvalidUsNewsCatalystCollectionProfileError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst collection profile binding is blocked"


@dataclass(frozen=True, slots=True)
class BoundUsNewsCatalystCollectionProfile:
    subscription: DesiredMarketDataSubscription
    profile: IntradayVolumeProfileEvidence


def bind_new_collection_profiles(
    subscriptions: tuple[DesiredMarketDataSubscription, ...],
    materialized: tuple[ProfileArtifactBinding, ...],
) -> tuple[BoundUsNewsCatalystCollectionProfile, ...]:
    paths = {item.instrument_id: item.path for item in materialized}
    if len(paths) != len(materialized) or set(paths) != {
        item.instrument_id for item in subscriptions
    }:
        raise InvalidUsNewsCatalystCollectionProfileError
    return tuple(
        BoundUsNewsCatalystCollectionProfile(
            subscription,
            IntradayVolumeProfileArtifactStore(
                paths[subscription.instrument_id].parent
            ).load(paths[subscription.instrument_id]),
        )
        for subscription in subscriptions
    )


def load_collection_profiles(
    plan: UsNewsCatalystCollectionPlan,
    subscriptions: tuple[DesiredMarketDataSubscription, ...],
    root: Path,
) -> tuple[BoundUsNewsCatalystCollectionProfile, ...]:
    hashes = {
        item.instrument_id: item.profile_evidence_sha256
        for item in plan.content.bindings
    }
    try:
        return tuple(
            BoundUsNewsCatalystCollectionProfile(
                subscription,
                load_materialized_profile(
                    root,
                    subscription,
                    hashes[subscription.instrument_id],
                ),
            )
            for subscription in subscriptions
        )
    except (KeyError, OSError, TypeError, ValueError):
        raise InvalidUsNewsCatalystCollectionProfileError from None


__all__ = (
    "BoundUsNewsCatalystCollectionProfile",
    "InvalidUsNewsCatalystCollectionProfileError",
    "bind_new_collection_profiles",
    "load_collection_profiles",
)

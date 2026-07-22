from __future__ import annotations

from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_theme_day_review_store import (
    KrThemeDayReviewStore,
    kr_theme_day_review_event_key,
)
from trading_agent.kr_theme_day_session_manifest import KrThemeDaySessionManifest
from trading_agent.kr_theme_day_terminal_delivery_state import (
    kr_theme_day_terminal_delivery_references,
)
from trading_agent.kr_theme_day_trial import kr_theme_day_trial_id
from trading_agent.kr_theme_day_trial_terminal_store import KrThemeDayTrialTerminalStore
from trading_agent.private_experiment_ledger_snapshot import open_private_experiment_ledger_snapshot


class InvalidKrThemeDayPostSessionSourceStateError(ValueError):
    pass


def kr_theme_day_post_session_references(
    manifest: KrThemeDaySessionManifest,
) -> tuple[str, ...]:
    trial_id = kr_theme_day_trial_id(manifest.session_date, manifest.strategy_version)
    with open_private_experiment_ledger_snapshot(manifest.paths.experiment_ledger) as ledger:
        events = ledger.multi_market_trial_events(trial_id)
        lifecycle = tuple(
            item
            for item in ledger.multi_market_lifecycle_events(manifest.strategy_version)
            if item.event.decision_session_date == manifest.session_date
        )
    artifacts = tuple(
        item
        for item in KrThemeDayTrialTerminalStore(manifest.paths.terminal_store).artifacts()
        if item.payload.trial_id == trial_id
    )
    reviews = tuple(
        event
        for event in KrThemeDayReviewStore(manifest.paths.review_store).events()
        if event.strategy_version == manifest.strategy_version and event.as_of_session == manifest.session_date
    )
    if len(events) != 2 or len(artifacts) != 1 or len(reviews) != 1 or not lifecycle:
        raise InvalidKrThemeDayPostSessionSourceStateError
    return (
        *(f"trial-event:{item.event_key}" for item in events),
        f"terminal:{artifacts[0].artifact_id}",
        *kr_theme_day_terminal_delivery_references(
            HermesDeliveryStore(manifest.paths.delivery_store),
            artifacts[0],
        ),
        f"review:{kr_theme_day_review_event_key(reviews[0])}",
        *(f"lifecycle:{item.event_key}" for item in lifecycle),
    )


__all__ = ("kr_theme_day_post_session_references",)

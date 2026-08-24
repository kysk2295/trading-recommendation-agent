from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, assert_never, override
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.day_discovery_loop import (
    DayDiscoveryEvidenceView,
    DayDiscoveryTriggerKind,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kis_kr_session_calendar import (
    InvalidKisKrSessionCalendarError,
    next_kr_open_session,
)
from trading_agent.kis_kr_session_calendar_models import KrSessionCalendarSnapshot
from trading_agent.kr_intraday_market_gate import KrMarketConstraintSnapshot
from trading_agent.kr_theme_day_setup import KrCompletedMinuteBar
from trading_agent.models import BarInput
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.research_agent_source_common import canonical_model_json
from trading_agent.research_identity_models import MarketId
from trading_agent.signal_contract_models import OpportunitySnapshot
from trading_agent.us_equity_calendar import (
    NEW_YORK,
    UnsupportedUsEquityCalendarDateError,
    next_regular_session,
    regular_session_bounds,
)
from trading_agent.us_strategy_day_input import (
    UsStrategyDayInput,
    spread_bps,
)

_KST: Final = ZoneInfo("Asia/Seoul")
_DISCOVERY_LEAD: Final = dt.timedelta(minutes=10)
_ONE_MINUTE: Final = dt.timedelta(minutes=1)
_LIVE_SOURCE_DELAY: Final = dt.timedelta(seconds=30)
_KR_OPEN: Final = dt.time(9)
_KR_CLOSE: Final = dt.time(15, 30)


class DayDiscoveryLiveProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "live Day Discovery evidence is invalid"


def project_us_live_discovery_evidence(
    source: UsStrategyDayInput,
    *,
    published_at: dt.datetime,
) -> DayDiscoveryEvidenceView:
    try:
        checked = UsStrategyDayInput.model_validate(source.model_dump(mode="python"))
        observed_at = _utc(published_at)
        candidate = checked.candidates[0]
        latest = candidate.bars[-1]
        completed_at = _utc(latest.timestamp + _ONE_MINUTE)
        local_date = completed_at.astimezone(NEW_YORK).date()
        bounds = regular_session_bounds(local_date)
        if (
            bounds is None
            or checked.materialized_at > published_at
            or completed_at > observed_at
            or candidate.received_at > observed_at
            or latest.timestamp.astimezone(NEW_YORK).date() != local_date
        ):
            raise DayDiscoveryLiveProjectionError
        prior = candidate.bars[:-1]
        average_minute_volume = max(1, sum(item.volume for item in prior) // len(prior))
        replay = BarInput(
            symbol=candidate.symbol,
            timestamp=_utc(latest.timestamp),
            open=latest.open,
            high=latest.high,
            low=latest.low,
            close=latest.close,
            volume=latest.volume,
            prior_close=candidate.bars[0].open,
            average_daily_volume=average_minute_volume * 390,
            spread_bps=float(spread_bps(candidate)),
        )
        symbols = tuple(item.symbol for item in checked.candidates)
        universe_digest = hashlib.sha256("|".join(symbols).encode()).hexdigest()[:24]
        source_refs = tuple(
            sorted(
                {
                    f"day_input:{checked.input_id}",
                    f"market_context:{checked.market_context.context_id}",
                    f"opportunity:{checked.opportunity.opportunity_id}",
                    *(item.canonical_id for item in checked.opportunity.evidence_refs),
                    *(item.canonical_id for item in candidate.evidence_refs),
                }
            )
        )
        return DayDiscoveryEvidenceView(
            market_id=MarketId.US_EQUITIES,
            trigger_kind=_trigger_kind(observed_at, completed_at),
            observed_at=observed_at,
            completed_bar_at=completed_at,
            first_eligible_completed_bar_at=_next_us_probe_bar(observed_at),
            universe_snapshot_id=f"us-fixed-etf-{local_date.isoformat()}-{universe_digest}",
            universe_snapshot_at=bounds[0].astimezone(dt.UTC),
            source_refs=source_refs,
            evidence_schema=(
                "completed_bar_v1",
                "current_quote_v1",
                "intraday_volume_reference_v1",
                "us_strategy_day_input_v1",
            ),
            data_manifest_sha256=_sha(canonical_experiment_ledger_json(checked)),
            replay_bars=(replay,),
            budget_epoch_ref=f"us-equities-{local_date.isoformat()}",
            search_budget=3,
        )
    except DayDiscoveryLiveProjectionError:
        raise
    except (
        ArithmeticError,
        AttributeError,
        IndexError,
        TypeError,
        UnsupportedUsEquityCalendarDateError,
        ValidationError,
        ValueError,
    ):
        raise DayDiscoveryLiveProjectionError from None


def project_kr_live_discovery_evidence(
    opportunity: OpportunitySnapshot,
    market: KrMarketConstraintSnapshot,
    bars: tuple[KrCompletedMinuteBar, ...],
    calendar: KrSessionCalendarSnapshot,
    *,
    published_at: dt.datetime,
) -> DayDiscoveryEvidenceView:
    try:
        checked_opportunity = OpportunitySnapshot.model_validate(opportunity.model_dump(mode="python"))
        checked_market = KrMarketConstraintSnapshot.model_validate(market.model_dump(mode="python"))
        checked_bars = tuple(KrCompletedMinuteBar.model_validate(item.model_dump(mode="python")) for item in bars)
        checked_calendar = KrSessionCalendarSnapshot.model_validate(calendar.model_dump(mode="python"))
        observed_at = _utc(published_at)
        latest = checked_bars[-1]
        completed_at = _utc(latest.end_at)
        candidate = checked_opportunity.candidates[0]
        features = {item.name: item.value for item in candidate.features}
        ratio = Decimal(features["volume_ratio"])
        if (
            not ratio.is_finite()
            or ratio <= 0
            or candidate.symbol != latest.symbol
            or checked_market.symbol != latest.symbol
            or checked_market.bid_price is None
            or checked_market.ask_price is None
            or max(
                checked_opportunity.observed_at,
                checked_market.observed_at,
                latest.observed_at,
            )
            > observed_at
            or completed_at > observed_at
        ):
            raise DayDiscoveryLiveProjectionError
        midpoint = (checked_market.bid_price + checked_market.ask_price) / Decimal(2)
        current_spread_bps = (checked_market.ask_price - checked_market.bid_price) / midpoint * Decimal(10_000)
        cumulative_volume = sum(item.volume for item in checked_bars)
        average_daily_volume = max(1, int(Decimal(cumulative_volume) / ratio))
        theme_name = features.get("theme_name", "")
        replay = tuple(
            BarInput(
                symbol=item.symbol,
                timestamp=_utc(item.start_at),
                open=float(item.open),
                high=float(item.high),
                low=float(item.low),
                close=float(item.close),
                volume=item.volume,
                prior_close=float(checked_market.previous_close),
                average_daily_volume=average_daily_volume,
                spread_bps=float(current_spread_bps),
                catalyst=theme_name,
            )
            for item in checked_bars[-6:]
        )
        local_date = completed_at.astimezone(_KST).date()
        source_refs = tuple(
            sorted(
                {
                    f"calendar:{checked_calendar.snapshot_id}",
                    f"opportunity:{checked_opportunity.opportunity_id}",
                    latest.evidence_ref.canonical_id,
                    *(item.canonical_id for item in checked_opportunity.evidence_refs),
                    *(item.canonical_id for item in checked_market.evidence_refs),
                }
            )
        )
        manifest = "|".join(
            (
                canonical_experiment_ledger_json(checked_opportunity),
                canonical_experiment_ledger_json(checked_market),
                *(canonical_experiment_ledger_json(item) for item in checked_bars[-6:]),
            )
        )
        return DayDiscoveryEvidenceView(
            market_id=MarketId.KR_EQUITIES,
            trigger_kind=_trigger_kind(observed_at, completed_at),
            observed_at=observed_at,
            completed_bar_at=completed_at,
            first_eligible_completed_bar_at=_next_kr_probe_bar(observed_at, checked_calendar),
            universe_snapshot_id=(
                f"kr-opportunity-{local_date.isoformat()}-"
                f"{hashlib.sha256(checked_opportunity.opportunity_id.encode()).hexdigest()[:24]}"
            ),
            universe_snapshot_at=completed_at,
            source_refs=source_refs,
            evidence_schema=(
                "completed_bar_v1",
                "fresh_spread_v1",
                "kr_read_only_market_v1",
                "kr_volume_ratio_derived_daily_reference_v1",
            ),
            data_manifest_sha256=_sha(manifest),
            replay_bars=replay,
            budget_epoch_ref=f"kr-equities-{local_date.isoformat()}",
            search_budget=3,
        )
    except DayDiscoveryLiveProjectionError:
        raise
    except (
        ArithmeticError,
        AttributeError,
        IndexError,
        InvalidKisKrSessionCalendarError,
        InvalidOperation,
        KeyError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise DayDiscoveryLiveProjectionError from None


def publish_live_discovery_evidence_once(
    live_session_root: Path,
    view: DayDiscoveryEvidenceView,
) -> tuple[Path, bool]:
    try:
        checked = DayDiscoveryEvidenceView.model_validate(view.model_dump(mode="python"))
        match checked.market_id:
            case MarketId.US_EQUITIES:
                session_name = checked.observed_at.astimezone(NEW_YORK).strftime("%Y%m%d")
            case MarketId.KR_EQUITIES:
                session_name = checked.observed_at.astimezone(_KST).strftime("%Y%m%d")
            case unreachable:
                assert_never(unreachable)
        target = (
            live_session_root.expanduser().absolute()
            / session_name
            / f"day-discovery-evidence.{checked.market_id.value}.v1.json"
        )
        if target.exists():
            stored = DayDiscoveryEvidenceView.model_validate_json(read_private_text(target))
            if stored.market_id is not checked.market_id or canonical_model_json(stored) != read_private_text(target):
                raise DayDiscoveryLiveProjectionError
            return target, False
        created = publish_private_immutable_text(target, canonical_model_json(checked))
        return target, created
    except DayDiscoveryLiveProjectionError:
        raise
    except (
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise DayDiscoveryLiveProjectionError from None


def _trigger_kind(observed_at: dt.datetime, completed_at: dt.datetime) -> DayDiscoveryTriggerKind:
    if observed_at - completed_at <= _LIVE_SOURCE_DELAY:
        return DayDiscoveryTriggerKind.COMPLETED_BAR
    return DayDiscoveryTriggerKind.POINT_IN_TIME_EVIDENCE


def _next_us_probe_bar(observed_at: dt.datetime) -> dt.datetime:
    local = observed_at.astimezone(NEW_YORK)
    bounds = regular_session_bounds(local.date())
    candidate = _ceil_minute(local + _DISCOVERY_LEAD)
    if bounds is not None and bounds[0] < candidate <= bounds[1]:
        return candidate.astimezone(dt.UTC)
    next_bounds = regular_session_bounds(next_regular_session(local.date()))
    if next_bounds is None:
        raise DayDiscoveryLiveProjectionError
    return (next_bounds[0] + _ONE_MINUTE).astimezone(dt.UTC)


def _next_kr_probe_bar(
    observed_at: dt.datetime,
    calendar: KrSessionCalendarSnapshot,
) -> dt.datetime:
    local = observed_at.astimezone(_KST)
    candidate = _ceil_minute(local + _DISCOVERY_LEAD)
    if _KR_OPEN < candidate.time() <= _KR_CLOSE:
        return candidate.astimezone(dt.UTC)
    next_session = next_kr_open_session(calendar, local.date())
    return dt.datetime.combine(next_session, _KR_OPEN, tzinfo=_KST).astimezone(dt.UTC) + _ONE_MINUTE


def _ceil_minute(value: dt.datetime) -> dt.datetime:
    floored = value.replace(second=0, microsecond=0)
    return floored if value == floored else floored + _ONE_MINUTE


def _utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DayDiscoveryLiveProjectionError
    return value.astimezone(dt.UTC)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


__all__ = (
    "DayDiscoveryLiveProjectionError",
    "project_kr_live_discovery_evidence",
    "project_us_live_discovery_evidence",
    "publish_live_discovery_evidence_once",
)

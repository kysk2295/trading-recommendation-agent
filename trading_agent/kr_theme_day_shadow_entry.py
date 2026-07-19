from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kr_theme_day_shadow_entry_models import (
    SHADOW_ENTRY_SLIPPAGE_BPS,
    InvalidKrThemeDayShadowEntryError,
    KrThemeDayShadowEntry,
)
from trading_agent.kr_theme_day_shadow_entry_store import KrThemeDayShadowEntryStore
from trading_agent.kr_theme_day_trial import require_exact_kr_theme_day_trial
from trading_agent.kr_theme_lane import KR_THEME_LEADER_VWAP_RECLAIM_LANE
from trading_agent.multi_market_trial_models import market_local_date
from trading_agent.signal_contract_models import (
    SignalActionability,
    SignalEntryType,
    SignalSide,
    TradeSignalEnvelope,
)


@dataclass(frozen=True, slots=True)
class KrThemeDayShadowEntryResult:
    created: bool
    entry: KrThemeDayShadowEntry


def project_kr_theme_day_shadow_entry(
    ledger: ExperimentLedgerStore,
    store: KrThemeDayShadowEntryStore,
    signal: TradeSignalEnvelope,
    *,
    filled_at: dt.datetime,
) -> KrThemeDayShadowEntryResult:
    try:
        signal = TradeSignalEnvelope.model_validate(signal.model_dump(mode="python"))
        _require_signal(signal, filled_at)
        trial_key, event_key, trial_id = _trial_binding(ledger, signal, filled_at)
        payload = canonical_experiment_ledger_json(signal)
        signal_sha = hashlib.sha256(payload.encode()).hexdigest()
        fill_price = signal.entry_price * (Decimal(1) + SHADOW_ENTRY_SLIPPAGE_BPS / Decimal(10_000))
        targets = tuple(target.price for target in signal.targets)
        evidence_ids = tuple(sorted(item.canonical_id for item in signal.evidence_refs))
        material = "|".join((trial_key, event_key, signal_sha, filled_at.isoformat(), str(fill_price)))
        entry = KrThemeDayShadowEntry(
            entry_id=hashlib.sha256(material.encode()).hexdigest(),
            trial_id=trial_id,
            trial_registration_key=trial_key,
            started_event_key=event_key,
            signal_id=signal.signal_id,
            signal_payload_sha256=signal_sha,
            strategy_version=signal.producer_strategy_version,
            symbol=signal.symbol,
            signal_observed_at=signal.observed_at,
            filled_at=filled_at,
            quoted_entry_price=signal.entry_price,
            slippage_bps=SHADOW_ENTRY_SLIPPAGE_BPS,
            fill_price=fill_price,
            stop_price=signal.stop_price,
            target_prices=targets,
            evidence_ids=evidence_ids,
        )
        created = store.append(entry)
    except (AttributeError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeDayShadowEntryError from None
    return KrThemeDayShadowEntryResult(created, entry)


def _require_signal(signal: TradeSignalEnvelope, filled_at: dt.datetime) -> None:
    quote = signal.quote_validation
    if (
        signal.strategy_lane != KR_THEME_LEADER_VWAP_RECLAIM_LANE
        or signal.side is not SignalSide.LONG
        or signal.entry_type is not SignalEntryType.LIMIT
        or signal.actionability is not SignalActionability.CURRENT_QUOTE_VALIDATED
        or quote is None
        or signal.entry_price != quote.ask
        or not _aware(filled_at)
        or not signal.observed_at <= filled_at < signal.valid_until
        or filled_at >= quote.valid_until
    ):
        raise InvalidKrThemeDayShadowEntryError


def _trial_binding(
    ledger: ExperimentLedgerStore,
    signal: TradeSignalEnvelope,
    filled_at: dt.datetime,
) -> tuple[str, str, str]:
    session_date = market_local_date(KR_THEME_LEADER_VWAP_RECLAIM_LANE.market_id, filled_at)
    matches = tuple(
        stored
        for stored in ledger.multi_market_trials()
        if stored.registration.strategy_version == signal.producer_strategy_version
        and stored.registration.planned_start == session_date
        and stored.registration.planned_end == session_date
    )
    if len(matches) != 1:
        raise InvalidKrThemeDayShadowEntryError
    trial = matches[0]
    require_exact_kr_theme_day_trial(ledger, trial.registration)
    events = ledger.multi_market_trial_events(trial.registration.trial_id)
    if (
        len(events) != 1
        or events[0].event.event_kind is not TrialEventKind.STARTED
        or events[0].event.occurred_at > filled_at
    ):
        raise InvalidKrThemeDayShadowEntryError
    return str(trial.registration_key), str(events[0].event_key), trial.registration.trial_id


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None

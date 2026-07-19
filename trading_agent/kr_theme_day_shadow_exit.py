from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from typing import Final
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_theme_day_setup import KrCompletedMinuteBar
from trading_agent.kr_theme_day_shadow_entry_models import KrThemeDayShadowEntry
from trading_agent.kr_theme_day_shadow_entry_store import KrThemeDayShadowEntryStore
from trading_agent.kr_theme_day_shadow_exit_models import (
    SHADOW_EXIT_SLIPPAGE_BPS,
    InvalidKrThemeDayShadowExitError,
    KrThemeDayShadowExit,
    KrThemeDayShadowExitReason,
)
from trading_agent.kr_theme_day_shadow_exit_store import KrThemeDayShadowExitStore

_KST: Final = ZoneInfo("Asia/Seoul")
_ONE_MINUTE: Final = dt.timedelta(minutes=1)
_SESSION_CLOSE: Final = dt.time(15, 30)


@dataclass(frozen=True, slots=True)
class KrThemeDayShadowExitResult:
    created: bool
    exit: KrThemeDayShadowExit


def project_kr_theme_day_shadow_exit(
    entry_store: KrThemeDayShadowEntryStore,
    exit_store: KrThemeDayShadowExitStore,
    entry_id: str,
    bars: tuple[KrCompletedMinuteBar, ...],
    *,
    evaluated_at: dt.datetime,
) -> KrThemeDayShadowExitResult | None:
    try:
        entry = _entry(entry_store, entry_id)
        validated = tuple(KrCompletedMinuteBar.model_validate(bar.model_dump(mode="python")) for bar in bars)
        _require_path(entry, validated, evaluated_at)
        terminal = _terminal(entry, validated)
        if terminal is None:
            return None
        reason, trigger, consumed = terminal
        exit_price = trigger * (Decimal(1) - SHADOW_EXIT_SLIPPAGE_BPS / Decimal(10_000))
        evidence_ids = tuple(bar.evidence_ref.canonical_id for bar in consumed)
        bar_hashes = tuple(
            hashlib.sha256(canonical_experiment_ledger_json(bar).encode()).hexdigest() for bar in consumed
        )
        material = "|".join((entry.entry_id, reason.value, consumed[-1].end_at.isoformat(), *bar_hashes))
        exit = KrThemeDayShadowExit(
            exit_id=hashlib.sha256(material.encode()).hexdigest(),
            entry_id=entry.entry_id,
            trial_id=entry.trial_id,
            signal_id=entry.signal_id,
            strategy_version=entry.strategy_version,
            symbol=entry.symbol,
            reason=reason,
            entry_fill_price=entry.fill_price,
            stop_price=entry.stop_price,
            first_target_price=entry.target_prices[0],
            trigger_price=trigger,
            exit_slippage_bps=SHADOW_EXIT_SLIPPAGE_BPS,
            exit_price=exit_price,
            exit_at=consumed[-1].end_at,
            evaluated_at=evaluated_at,
            net_return=exit_price / entry.fill_price - Decimal(1),
            realized_r=(exit_price - entry.fill_price) / (entry.fill_price - entry.stop_price),
            bar_evidence_ids=evidence_ids,
            bar_payload_sha256s=bar_hashes,
        )
        created = exit_store.append(exit)
    except (AttributeError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeDayShadowExitError from None
    return KrThemeDayShadowExitResult(created, exit)


def _entry(store: KrThemeDayShadowEntryStore, entry_id: str) -> KrThemeDayShadowEntry:
    matches = tuple(entry for entry in store.entries() if entry.entry_id == entry_id)
    if len(matches) != 1:
        raise InvalidKrThemeDayShadowExitError
    return matches[0]


def _require_path(
    entry: KrThemeDayShadowEntry,
    bars: tuple[KrCompletedMinuteBar, ...],
    evaluated_at: dt.datetime,
) -> None:
    if not bars or not _aware(evaluated_at):
        raise InvalidKrThemeDayShadowExitError
    first_start = _first_full_bar_start(entry.filled_at)
    if bars[0].start_at.astimezone(_KST) != first_start:
        raise InvalidKrThemeDayShadowExitError
    for bar in bars:
        if (
            bar.symbol != entry.symbol
            or bar.observed_at > evaluated_at
            or bar.start_at.astimezone(_KST).date() != first_start.date()
        ):
            raise InvalidKrThemeDayShadowExitError
    if any(left.end_at != right.start_at for left, right in pairwise(bars)):
        raise InvalidKrThemeDayShadowExitError


def _terminal(
    entry: KrThemeDayShadowEntry,
    bars: tuple[KrCompletedMinuteBar, ...],
) -> tuple[KrThemeDayShadowExitReason, Decimal, tuple[KrCompletedMinuteBar, ...]] | None:
    for index, bar in enumerate(bars):
        consumed = bars[: index + 1]
        if bar.low <= entry.stop_price:
            return KrThemeDayShadowExitReason.STOPPED, entry.stop_price, consumed
        if bar.high >= entry.target_prices[0]:
            return KrThemeDayShadowExitReason.TARGETED, entry.target_prices[0], consumed
    if bars[-1].end_at.astimezone(_KST).time() == _SESSION_CLOSE:
        return KrThemeDayShadowExitReason.TIME_EXIT, bars[-1].close, bars
    return None


def _first_full_bar_start(filled_at: dt.datetime) -> dt.datetime:
    local = filled_at.astimezone(_KST)
    floor = local.replace(second=0, microsecond=0)
    return floor if local == floor else floor + _ONE_MINUTE


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None

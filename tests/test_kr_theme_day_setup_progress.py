from __future__ import annotations

import datetime as dt
from decimal import Decimal

from trading_agent.kr_price_grid import is_valid_kr_equity_price
from trading_agent.kr_theme_day_setup import (
    KrCompletedMinuteBar,
    KrThemeDaySetupInput,
    KrThemeDaySetupPhase,
    assess_kr_theme_day_setup,
    derive_kr_theme_day_setup,
)
from trading_agent.kr_theme_lane import KR_THEME_OPPORTUNITY_LANE
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)

SEOUL = dt.timezone(dt.timedelta(hours=9))
SESSION = dt.datetime(2026, 7, 20, 9, 0, tzinfo=SEOUL)


def test_assessment_reports_no_impulse_when_extension_never_occurs() -> None:
    # Given only a completed bar that closes at session VWAP
    source = _input((_bar(0, "50000", "50100", "49900", "50000", 100, "5000000"),))

    # When setup progression is assessed
    assessment = assess_kr_theme_day_setup(source)

    # Then the absence of an impulse is explicit and evidence-bearing
    assert assessment.phase is KrThemeDaySetupPhase.NO_IMPULSE
    assert assessment.model_dump(mode="json")["phase"] == "NO_IMPULSE"
    assert assessment.conditional is None
    assert assessment.setup is None
    assert _evidence_ids(assessment.evidence_refs) == ("kr/minute/bar:bar-1",)


def test_assessment_reports_impulse_only_before_first_vwap_pullback() -> None:
    # Given a qualifying extension without a VWAP pullback
    source = _input(_bars()[:2])

    # When setup progression is assessed
    assessment = assess_kr_theme_day_setup(source)

    # Then the impulse-only state is explicit
    assert assessment.phase is KrThemeDaySetupPhase.IMPULSE_ONLY
    assert assessment.conditional is None
    assert assessment.setup is None


def test_pullback_assessment_has_grid_normalized_conditional_plan() -> None:
    # Given an impulse followed by the first completed-bar VWAP pullback
    source = _input(_bars()[:3])

    # When setup progression is assessed
    assessment = assess_kr_theme_day_setup(source)

    # Then it exposes the truthful unfilled reclaim condition and bounded levels
    assert assessment.phase is KrThemeDaySetupPhase.PULLBACK_FOUND
    assert assessment.setup is None
    assert assessment.conditional is not None
    assert (
        assessment.conditional.trigger_rule
        == "A completed bar must close above the current session VWAP reclaim buffer, "
        "trade above the pullback high, close green, and confirm volume."
    )
    assert assessment.conditional.trigger_price == Decimal("51100")
    assert assessment.conditional.stop_price == Decimal("50000")
    assert assessment.conditional.target_prices == (Decimal("52200"), Decimal("53300"))
    assert (
        assessment.conditional.invalidation_rule
        == "Invalidate below the first completed-bar VWAP pullback low or after five reclaim bars."
    )
    assert assessment.conditional.valid_until == SESSION + dt.timedelta(minutes=8)
    assert _evidence_ids(assessment.conditional.evidence_refs) == (
        "kr/minute/bar:bar-1",
        "kr/minute/bar:bar-2",
        "kr/minute/bar:bar-3",
    )
    assert all(
        is_valid_kr_equity_price(price)
        for price in (
            assessment.conditional.trigger_price,
            assessment.conditional.stop_price,
            *assessment.conditional.target_prices,
        )
    )


def test_latest_completed_bar_reclaim_confirms_compatible_setup() -> None:
    # Given a reclaim on the latest completed bar
    source = _input(_bars())

    # When assessed and derived through the public compatibility API
    assessment = assess_kr_theme_day_setup(source)
    setup = derive_kr_theme_day_setup(source)

    # Then the assessment carries that same final setup with grid-valid levels
    assert assessment.phase is KrThemeDaySetupPhase.RECLAIM_CONFIRMED
    assert assessment.setup == setup
    assert setup is not None
    assert setup.stop_price == Decimal("50000")
    assert tuple(target.price for target in setup.targets) == (Decimal("53000"), Decimal("54500"))
    assert setup.valid_until == SESSION + dt.timedelta(minutes=4, seconds=31)
    assert all(is_valid_kr_equity_price(target.price) for target in setup.targets)


def test_invalidated_pullback_reports_expired_without_plan() -> None:
    # Given a pullback followed by a completed bar closing below its VWAP tolerance
    invalidation = _bar(3, "50000", "50100", "49500", "49900", 120, "5988000")
    source = _input((*_bars()[:3], invalidation))

    # When setup progression is assessed
    assessment = assess_kr_theme_day_setup(source)

    # Then it is terminal and cannot retain a conditional plan
    assert assessment.phase is KrThemeDaySetupPhase.SETUP_EXPIRED
    assert assessment.conditional is None
    assert assessment.setup is None


def test_six_bars_after_pullback_expire_reclaim_window() -> None:
    # Given six non-reclaim completed bars after a pullback
    waiting = tuple(_bar(minute, "50300", "50900", "50200", "50400", 100, "5040000") for minute in range(3, 9))
    source = _input((*_bars()[:3], *waiting))

    # When setup progression is assessed
    assessment = assess_kr_theme_day_setup(source)

    # Then the five-bar reclaim window is terminally expired
    assert assessment.phase is KrThemeDaySetupPhase.SETUP_EXPIRED
    assert assessment.conditional is None
    assert assessment.setup is None


def test_earlier_bar_reclaim_is_never_backdated() -> None:
    # Given a valid reclaim followed by another completed bar
    later = _bar(4, "51500", "51600", "51000", "51200", 100, "5120000")
    source = _input((*_bars(), later))

    # When setup progression is assessed and derived
    assessment = assess_kr_theme_day_setup(source)
    setup = derive_kr_theme_day_setup(source)

    # Then the missed latest-bar opportunity is terminal rather than backdated
    assert assessment.phase is KrThemeDaySetupPhase.SETUP_EXPIRED
    assert assessment.setup is None
    assert setup is None


def test_near_close_pullback_condition_ends_at_same_session_close() -> None:
    # Given a completed VWAP pullback one minute before the Seoul session close
    source = _near_close_pullback_input()

    # When setup progression is assessed
    assessment = assess_kr_theme_day_setup(source)

    # Then its conditional validity cannot extend into the closed session
    assert assessment.phase is KrThemeDaySetupPhase.PULLBACK_FOUND
    assert assessment.conditional is not None
    assert assessment.conditional.valid_until == SESSION.replace(hour=15, minute=30)


def test_close_bar_reclaim_cannot_confirm_setup_after_session_close() -> None:
    # Given a valid reclaim on the 15:29-15:30 completed bar
    pullback_source = _near_close_pullback_input()
    reclaim = _bar(389, "244000", "245500", "244000", "245000", 180, "44100000")
    source = pullback_source.model_copy(
        update={
            "bars": (*pullback_source.bars, reclaim),
            "evaluated_at": reclaim.observed_at + dt.timedelta(seconds=1),
        }
    )

    # When setup progression is assessed after that completed bar
    assessment = assess_kr_theme_day_setup(source)

    # Then the closed-session setup is expired rather than valid after 15:30
    assert assessment.phase is KrThemeDaySetupPhase.SETUP_EXPIRED
    assert assessment.setup is None


def _input(bars: tuple[KrCompletedMinuteBar, ...]) -> KrThemeDaySetupInput:
    return KrThemeDaySetupInput(
        opportunity=_opportunity(),
        bars=bars,
        producer_strategy_version="kr-theme-leader-vwap-reclaim-v1",
        evaluated_at=bars[-1].observed_at + dt.timedelta(seconds=1),
        max_slippage_bps=Decimal("20"),
    )


def _near_close_pullback_input() -> KrThemeDaySetupInput:
    impulse = _bar(0, "50000", "50500", "50000", "50500", 100, "5000000")
    extensions = tuple(
        _bar(
            minute,
            str(50000 + 1000 * minute),
            str(50000 + 1000 * minute),
            str(50000 + 1000 * minute),
            str(50000 + 1000 * minute),
            100,
            str((50000 + 1000 * minute) * 100),
        )
        for minute in range(1, 388)
    )
    pullback = _bar(388, "243500", "244000", "243000", "243500", 100, "24350000")
    bars = (impulse, *extensions, pullback)
    opportunity = _opportunity().model_copy(update={"valid_until": SESSION.replace(hour=16)})
    return _input(bars).model_copy(update={"opportunity": opportunity})


def _bars() -> tuple[KrCompletedMinuteBar, ...]:
    return (
        _bar(0, "50000", "50500", "49500", "50500", 100, "5000000"),
        _bar(1, "50500", "51500", "50000", "51000", 100, "5050000"),
        _bar(2, "51000", "51000", "50000", "50400", 100, "5040000"),
        _bar(3, "50500", "52000", "50500", "51500", 180, "9180000"),
    )


def _bar(
    minute: int,
    open_price: str,
    high: str,
    low: str,
    close: str,
    volume: int,
    trading_value: str,
) -> KrCompletedMinuteBar:
    start = SESSION + dt.timedelta(minutes=minute)
    observed = start + dt.timedelta(minutes=1, seconds=1)
    return KrCompletedMinuteBar(
        symbol="005930",
        start_at=start,
        end_at=start + dt.timedelta(minutes=1),
        observed_at=observed,
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
        trading_value_krw=Decimal(trading_value),
        evidence_ref=EvidenceRef(namespace="kr/minute/bar", record_id=f"bar-{minute + 1}", observed_at=observed),
    )


def _opportunity() -> OpportunitySnapshot:
    observed = SESSION - dt.timedelta(minutes=1)
    return OpportunitySnapshot(
        opportunity_id="KR-THEME-OPPORTUNITY-001",
        strategy_lane=KR_THEME_OPPORTUNITY_LANE,
        producer_strategy_version="kr-theme-manager-v1",
        observed_at=observed,
        valid_until=SESSION + dt.timedelta(minutes=10),
        candidates=(
            OpportunityCandidate(
                symbol="005930",
                rank=1,
                score=Decimal("100"),
                features=(FeatureValue(name="theme_name", value="semiconductor"),),
            ),
        ),
        evidence_refs=(EvidenceRef(namespace="kr/theme/state", record_id="theme-1", observed_at=observed),),
        source_coverage=(SourceCoverage(source_id="kr_theme", observed_at=observed, record_count=1, complete=True),),
    )


def _evidence_ids(refs: tuple[EvidenceRef, ...]) -> tuple[str, ...]:
    return tuple(ref.canonical_id for ref in refs)

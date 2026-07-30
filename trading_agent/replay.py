from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path

from trading_agent.causality import first_eligible_bar_at
from trading_agent.kis_live import NEW_YORK
from trading_agent.models import (
    BarInput,
    Recommendation,
    RecommendationAlert,
    RecommendationState,
)
from trading_agent.store import PaperStore

_MAX_BOUNDED_REPLAY_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class InvalidBarTimestampError(ValueError):
    timestamp: str

    def __str__(self) -> str:
        return f"timestamp에 UTC offset이 필요합니다: {self.timestamp}"


@dataclass(frozen=True, slots=True)
class BoundedReplaySourceError(ValueError):
    reason: str

    def __str__(self) -> str:
        return f"bounded replay source rejected: {self.reason}"


@dataclass(frozen=True, slots=True)
class BoundedBarSource:
    bars: tuple[BarInput, ...]
    sha256: str


def load_bars(path: Path) -> tuple[BarInput, ...]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    bars = tuple(_bar_from_row(row) for row in rows)
    return tuple(sorted(bars, key=lambda row: (row.timestamp, row.symbol)))


def load_bounded_bars(
    path: Path,
    *,
    max_rows: int,
    max_sessions: int,
) -> tuple[BarInput, ...]:
    return load_bounded_bar_source(
        path,
        max_rows=max_rows,
        max_sessions=max_sessions,
    ).bars


def load_bounded_bar_source(
    path: Path,
    *,
    max_rows: int,
    max_sessions: int,
) -> BoundedBarSource:
    try:
        source = path.resolve(strict=True)
        if max_rows < 1 or max_sessions < 1 or "regend_us_stocks" in source.parts:
            raise BoundedReplaySourceError("unsafe_source_or_budget")
        raw_bytes = source.read_bytes()
        if len(raw_bytes) > _MAX_BOUNDED_REPLAY_BYTES:
            raise BoundedReplaySourceError("byte_budget_exceeded")
        bars: list[BarInput] = []
        sessions: set[dt.date] = set()
        handle = io.StringIO(raw_bytes.decode("utf-8"), newline="")
        for index, raw in enumerate(csv.DictReader(handle)):
            if index >= max_rows:
                raise BoundedReplaySourceError("row_budget_exceeded")
            row = {key: value or "" for key, value in raw.items()}
            bar = _bar_from_row(row)
            bars.append(bar)
            sessions.add(bar.timestamp.astimezone(NEW_YORK).date())
            if len(sessions) > max_sessions:
                raise BoundedReplaySourceError("session_budget_exceeded")
        if not bars:
            raise BoundedReplaySourceError("empty_source")
        return BoundedBarSource(
            bars=tuple(sorted(bars, key=lambda row: (row.timestamp, row.symbol))),
            sha256=hashlib.sha256(raw_bytes).hexdigest(),
        )
    except BoundedReplaySourceError:
        raise
    except (KeyError, OSError, TypeError, UnicodeError, ValueError):
        raise BoundedReplaySourceError("invalid_source") from None


def write_report(path: Path, store: PaperStore) -> None:
    lines = [
        "# 급등주 Paper 추천 재생 결과",
        "",
        "> 자동주문 또는 수익 보장이 아닌 연구용 조건부 추천 기록입니다.",
        "> 이 카드는 주문 권한이 아니며 현재 호가 검증 없이는 현재 진입 가능으로 표시하지 않습니다.",
        "",
    ]
    recommendations = store.recommendations()
    if not recommendations:
        lines.extend(
            (
                "추천 없음",
                "",
                "- 해석: 현재 원장에 저장된 조건부 추천이 없습니다.",
                "- 확인: 스캔 요약의 위험 게이트·스프레드·최신 완료 봉 조건을 함께 봅니다.",
                "- 주문: Paper mutation을 이 빈 결과로 실행하지 않습니다.",
            )
        )
    for row in recommendations:
        risk = row.entry - row.stop
        lines.extend(
            (
                f"## {row.symbol} · {_strategy_label(row.strategy)}",
                "",
                f"- 추천 ID: {row.recommendation_id}",
                f"- 시장: {MARKET_ID}",
                f"- 에이전트: {AGENT_FAMILY}",
                f"- 전략 lane: {_strategy_lane(row.strategy)}",
                f"- 전략 코드: {row.strategy}",
                f"- 생성 시각: {row.created_at.isoformat()}",
                f"- 상태: {_state_name(row.state)}",
                "- 실행 가능성: 조건부 (현재 호가 미검증 · Paper 전용)",
                "- 주문 권한: 없음 (추천 카드 자체는 주문하지 않음)",
                f"- 조건부 진입가: {_price(row.entry)}",
                f"- 손절가: {_price(row.stop)}",
                f"- 1R 목표가: {_price(row.target_1r)}",
                f"- 2R 목표가: {_price(row.target_2r)}",
                f"- 주당 계획위험(R): {_price(risk)}",
                f"- 예상 보유: {EXPECTED_HOLD}",
                "- 무효화: 진입 전 손절가 이하, 정규장 종료, 데이터 지연·호가 결손",
                "- 같은 봉 충돌: 손절과 목표가 동시 도달 시 손절 우선",
                f"- 근거: {row.rationale}",
                "",
                "### 이벤트",
                "",
            )
        )
        lines.extend(
            f"- {event.occurred_at.isoformat()} · {_state_name(event.state)}"
            + ("" if event.price is None else f" · {_price(event.price)}")
            + ("" if event.note == "" else f" · {event.note}")
            for event in store.events(row.recommendation_id)
        )
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text("\n".join(lines), encoding="utf-8")


def write_alert_outbox(
    output_dir: Path,
    store: PaperStore,
    queued_at: dt.datetime,
    created_after: dt.datetime | None = None,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    queued = sum(
        store.queue_alert(_recommendation_alert(row, queued_at))
        for row in store.recommendations()
        if row.state is not RecommendationState.CAUSALITY_EXCLUDED
        and (created_after is None or row.created_at >= created_after)
    )
    alerts = store.alerts()
    jsonl = "\n".join(alert.payload_json for alert in alerts)
    if jsonl:
        jsonl += "\n"
    _ = (output_dir / "recommendation_alerts.jsonl").write_text(
        jsonl,
        encoding="utf-8",
    )
    markdown = [
        "# 급등주 Paper 추천 카드 Outbox",
        "",
        "> 자동주문 또는 수익 보장이 아닌 조건부 paper alert입니다.",
        "",
    ]
    if not alerts:
        markdown.append("새 추천 없음")
    else:
        markdown.extend(alert.card_markdown for alert in alerts)
    _ = (output_dir / "recommendation_alerts_ko.md").write_text(
        "\n".join(markdown),
        encoding="utf-8",
    )
    return queued


def _recommendation_alert(
    row: Recommendation,
    queued_at: dt.datetime,
) -> RecommendationAlert:
    effective_queued_at = max(row.created_at, queued_at)
    first_eligible_at = first_eligible_bar_at(effective_queued_at)
    risk = row.entry - row.stop
    strategy_lane = _strategy_lane(row.strategy)
    entry_condition = (
        f"알림 이후 새 완료 1분봉(첫 평가 가능 시작 {first_eligible_at.isoformat()})에서 "
        f"{_price(row.entry)} 이상 체결되고 세션·호가·스프레드 필터가 유효할 때만 paper 진입"
    )
    invalidation = (
        f"진입 전 {_price(row.stop)} 이하 도달, 정규장 종료, 데이터 지연·호가 결손 중 하나면 무효"
    )
    payload = json.dumps(
        {
            "schema_version": 1,
            "recommendation_id": row.recommendation_id,
            "paper_only": True,
            "order_authority": False,
            "current_entry_possible": False,
            "actionability": "conditional",
            "market_id": MARKET_ID,
            "agent_family": AGENT_FAMILY,
            "strategy_lane": strategy_lane,
            "symbol": row.symbol,
            "strategy": row.strategy,
            "created_at": row.created_at.isoformat(),
            "first_eligible_bar_at": first_eligible_at.isoformat(),
            "queued_at": effective_queued_at.isoformat(),
            "expected_hold": EXPECTED_HOLD,
            "entry_condition": entry_condition,
            "entry": round(row.entry, 6),
            "stop": round(row.stop, 6),
            "target_1r": round(row.target_1r, 6),
            "target_2r": round(row.target_2r, 6),
            "risk_per_share": round(risk, 6),
            "same_bar_collision_policy": "stop_first",
            "invalidation_condition": invalidation,
            "rationale": row.rationale,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    markdown = "\n".join(
        (
            f"## {row.symbol} · {_strategy_label(row.strategy)}",
            "",
            f"- 추천 ID: {row.recommendation_id}",
            f"- 시장: {MARKET_ID}",
            f"- 에이전트: {AGENT_FAMILY}",
            f"- 전략 lane: {strategy_lane}",
            f"- 전략 코드: {row.strategy}",
            f"- 알림 시각: {effective_queued_at.isoformat()}",
            f"- 첫 체결 평가 봉: {first_eligible_at.isoformat()}",
            "- 실행 가능성: 조건부 (현재 호가 미검증 · Paper 전용)",
            "- 현재 진입 가능: 아니오 (호가·세션·스프레드 재검증 전)",
            "- 주문 권한: 없음",
            f"- 조건부 진입: {_price(row.entry)}",
            f"- 진입 조건: {entry_condition}",
            f"- 손절: {_price(row.stop)}",
            f"- 목표: 1R {_price(row.target_1r)} / 2R {_price(row.target_2r)}",
            f"- 주당 계획위험(R): {_price(risk)}",
            f"- 예상 보유: {EXPECTED_HOLD}",
            f"- 무효화: {invalidation}",
            "- 같은 봉 충돌: 손절 우선",
            f"- 근거: {row.rationale}",
            "",
        )
    )
    return RecommendationAlert(
        row.recommendation_id,
        effective_queued_at,
        payload,
        markdown,
    )


MARKET_ID = "us_equities"
AGENT_FAMILY = "day_trading"
EXPECTED_HOLD = "당일 정규장 종료 전 time_exit (overnight 없음)"

_STRATEGY_LANES: dict[str, str] = {
    "opening_range_breakout": "us_equities/day_trading/orb",
    "vwap_reclaim": "us_equities/day_trading/vwap_reclaim",
    "hod_breakout": "us_equities/day_trading/hod_breakout",
    "gap_and_go": "us_equities/day_trading/gap_and_go",
}

_STRATEGY_LABELS: dict[str, str] = {
    "opening_range_breakout": "ORB 5분 돌파",
    "vwap_reclaim": "VWAP 첫 눌림 재탈환",
    "hod_breakout": "HOD 돌파",
    "gap_and_go": "갭 지속",
}


def _strategy_lane(strategy: str) -> str:
    return _STRATEGY_LANES.get(strategy, f"{MARKET_ID}/{AGENT_FAMILY}/{strategy}")


def _strategy_label(strategy: str) -> str:
    return _STRATEGY_LABELS.get(strategy, strategy)


def _price(value: float) -> str:
    return f"{value:.4f}"


def _bar_from_row(row: dict[str, str]) -> BarInput:
    timestamp = dt.datetime.fromisoformat(row["timestamp"])
    if timestamp.tzinfo is None:
        raise InvalidBarTimestampError(row["timestamp"])
    return BarInput(
        symbol=row["symbol"],
        timestamp=timestamp,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=int(row["volume"]),
        prior_close=float(row["prior_close"]),
        average_daily_volume=int(row["average_daily_volume"]),
        spread_bps=float(row["spread_bps"]),
        catalyst=row.get("catalyst", ""),
    )


def _state_name(state: RecommendationState) -> str:
    names = {
        RecommendationState.SETUP: "조건 대기",
        RecommendationState.ACTIVE: "진입 조건 충족",
        RecommendationState.INVALIDATED: "진입 전 무효",
        RecommendationState.CAUSALITY_EXCLUDED: "인과성 성과 제외",
        RecommendationState.STOPPED: "손절",
        RecommendationState.TARGET_1R: "1R 도달",
        RecommendationState.TARGET_2R: "2R 도달",
        RecommendationState.TIME_EXIT: "장 마감 종료",
    }
    return names[state]

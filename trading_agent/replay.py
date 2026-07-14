from __future__ import annotations

import csv
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from trading_agent.causality import first_eligible_bar_at
from trading_agent.models import (
    BarInput,
    Recommendation,
    RecommendationAlert,
    RecommendationState,
)
from trading_agent.store import PaperStore


@dataclass(frozen=True, slots=True)
class InvalidBarTimestampError(ValueError):
    timestamp: str

    def __str__(self) -> str:
        return f"timestamp에 UTC offset이 필요합니다: {self.timestamp}"


def load_bars(path: Path) -> tuple[BarInput, ...]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    bars = tuple(_bar_from_row(row) for row in rows)
    return tuple(sorted(bars, key=lambda row: (row.timestamp, row.symbol)))


def write_report(path: Path, store: PaperStore) -> None:
    lines = [
        "# 급등주 Paper 추천 재생 결과",
        "",
        "> 자동주문 또는 수익 보장이 아닌 연구용 조건부 추천 기록입니다.",
        "",
    ]
    recommendations = store.recommendations()
    if not recommendations:
        lines.append("추천 없음")
    for row in recommendations:
        lines.extend(
            (
                f"## {row.symbol} · {row.strategy}",
                "",
                f"- 생성 시각: {row.created_at.isoformat()}",
                f"- 상태: {_state_name(row.state)}",
                f"- 조건부 진입가: {row.entry:.4f}",
                f"- 손절가: {row.stop:.4f}",
                f"- 1R 목표가: {row.target_1r:.4f}",
                f"- 2R 목표가: {row.target_2r:.4f}",
                f"- 근거: {row.rationale}",
                "",
                "### 이벤트",
                "",
            )
        )
        lines.extend(
            f"- {event.occurred_at.isoformat()} · {_state_name(event.state)}"
            + ("" if event.price is None else f" · {event.price:.4f}")
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
    entry_condition = (
        f"알림 이후 새 완료 1분봉(첫 평가 가능 시작 {first_eligible_at.isoformat()})에서 "
        f"{row.entry:.4f} 이상 체결되고 세션·호가·스프레드 필터가 유효할 때만 paper 진입"
    )
    invalidation = f"진입 전 {row.stop:.4f} 이하 도달, 정규장 종료, 데이터 지연·호가 결손 중 하나면 무효"
    payload = json.dumps(
        {
            "schema_version": 1,
            "recommendation_id": row.recommendation_id,
            "paper_only": True,
            "symbol": row.symbol,
            "strategy": row.strategy,
            "created_at": row.created_at.isoformat(),
            "first_eligible_bar_at": first_eligible_at.isoformat(),
            "queued_at": effective_queued_at.isoformat(),
            "entry_condition": entry_condition,
            "entry": round(row.entry, 6),
            "stop": round(row.stop, 6),
            "target_1r": round(row.target_1r, 6),
            "target_2r": round(row.target_2r, 6),
            "risk_per_share": round(row.entry - row.stop, 6),
            "invalidation_condition": invalidation,
            "rationale": row.rationale,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    markdown = "\n".join(
        (
            f"## {row.symbol} · {row.strategy}",
            "",
            f"- 알림 시각: {effective_queued_at.isoformat()}",
            f"- 첫 체결 평가 봉: {first_eligible_at.isoformat()}",
            f"- 조건부 진입: {row.entry:.4f}",
            f"- 진입 조건: {entry_condition}",
            f"- 손절: {row.stop:.4f}",
            f"- 목표: 1R {row.target_1r:.4f} / 2R {row.target_2r:.4f}",
            f"- 무효화: {invalidation}",
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

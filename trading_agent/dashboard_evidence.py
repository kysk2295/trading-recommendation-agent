from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from pathlib import Path
from typing import Final, Literal

from trading_agent.dashboard_models import RecommendationView, ResearchView, SignalView

MAX_RECOMMENDATIONS: Final = 12
MAX_SIGNALS: Final = 12
SESSION_DIRECTORY = re.compile(r"^\d{8}$")
SELECT_RECOMMENDATIONS: Final = (
    "SELECT symbol, strategy, created_at, entry, stop, target_1r, "
    "target_2r, state, rationale FROM recommendations "
    "ORDER BY created_at DESC, symbol LIMIT ?"
)


def recommendations(session: Path | None) -> tuple[RecommendationView, ...]:
    if session is None:
        return ()
    database = session / "paper_recommendations.sqlite3"
    if not database.is_file():
        return ()
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                SELECT_RECOMMENDATIONS,
                (MAX_RECOMMENDATIONS,),
            ).fetchall()
    except sqlite3.Error:
        return ()
    return tuple(
        RecommendationView(
            symbol=str(row[0]),
            strategy=str(row[1]),
            created_at=dt.datetime.fromisoformat(str(row[2])),
            entry=float(row[3]),
            stop=float(row[4]),
            target_1r=float(row[5]),
            target_2r=float(row[6]),
            state=str(row[7]),
            rationale=str(row[8])[:240],
        )
        for row in rows
    )


def signals(session: Path | None) -> tuple[SignalView, ...]:
    if session is None:
        return ()
    path = session / "trade-signals.v1.jsonl"
    if not path.is_file():
        return ()
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()[-MAX_SIGNALS:]
        payloads = tuple(json.loads(line) for line in reversed(raw_lines) if line)
        return tuple(_signal_view(payload) for payload in payloads)
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return ()


def _signal_view(payload: object) -> SignalView:
    if not isinstance(payload, dict) or not isinstance(payload.get("signal"), dict):
        raise ValueError("invalid signal")
    signal = payload["signal"]
    strategy_lane = signal["strategy_lane"]
    targets = signal["targets"]
    evidence = signal["evidence_refs"]
    if not isinstance(strategy_lane, dict) or not isinstance(targets, list) or not isinstance(evidence, list):
        raise ValueError("invalid signal")
    return SignalView(
        symbol=str(signal["symbol"]),
        side=str(signal["side"]),
        strategy=str(strategy_lane["strategy_id"]),
        observed_at=dt.datetime.fromisoformat(str(signal["observed_at"])),
        valid_until=dt.datetime.fromisoformat(str(signal["valid_until"])),
        entry_price=str(signal["entry_price"]),
        stop_price=str(signal["stop_price"]),
        targets=tuple(str(item["price"]) for item in targets if isinstance(item, dict)),
        actionability=str(signal["actionability"]),
        rationale=str(signal["rationale"])[:240],
        evidence_namespaces=tuple(
            sorted(
                {
                    str(item["namespace"])
                    for item in evidence
                    if isinstance(item, dict) and "namespace" in item
                }
            )
        ),
    )


def research_view(outputs: Path) -> ResearchView:
    root = outputs / "experiment_control" / "source_intraday" / "latest"
    if not root.is_dir():
        return ResearchView(status="unavailable", session_date=None, summary="실제 연구 산출물 없음")
    dated = tuple(
        path for path in root.iterdir() if path.is_dir() and SESSION_DIRECTORY.fullmatch(path.name)
    )
    if not dated:
        return ResearchView(status="unavailable", session_date=None, summary="실제 연구 산출물 없음")
    latest = max(dated, key=lambda path: path.name)
    report = latest / "intraday_actual_research_ko.md"
    session_date = dt.datetime.strptime(latest.name, "%Y%m%d").date()
    if not report.is_file():
        return ResearchView(status="pending", session_date=session_date, summary="실제 연구 보고서 대기")
    try:
        text = report.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        text = ""
    status: Literal["ready", "blocked", "pending", "unavailable"]
    status = "ready" if "- result: ready" in text else "blocked" if "- result: blocked" in text else "pending"
    return ResearchView(
        status=status,
        session_date=session_date,
        summary="실제 causal 연구 기반 준비" if status == "ready" else "실제 causal 연구 기반 게이트 차단",
    )

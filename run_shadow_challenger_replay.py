#!/usr/bin/env -S uv run --python 3.12 --with rich --with typer python

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich import print as rprint

from trading_agent.challenger_replay_models import ChallengerReplayGate, ReplaySourceRejectedError
from trading_agent.challenger_replay_runner import run_challenger_replay
from trading_agent.challenger_replay_source import load_replay_source
from trading_agent.strategy_factory import StrategyMode


def main(
    input_dir: str,
    strategy: Annotated[StrategyMode, typer.Option()],
    output_dir: Annotated[str, typer.Option()],
) -> None:
    output = Path(output_dir)
    if strategy is StrategyMode.ORB:
        _fail(output, strategy, ("orb_is_champion_not_challenger",))
    try:
        source = load_replay_source(Path(input_dir))
    except ReplaySourceRejectedError as error:
        _fail(output, strategy, error.reasons, error.session_date)
    try:
        recommendations, trades = run_challenger_replay(source, strategy, output)
    except FileExistsError:
        _fail(output, strategy, ("output_database_already_exists",), source.session_date)
    complete = sum(row.complete for row in source.coverage)
    gate = ChallengerReplayGate(
        strategy=strategy.value,
        passed=True,
        session_date=source.session_date,
        reasons=("portfolio_comparison_not_implemented",),
        input_snapshots=len(source.contexts),
        complete_symbols=complete,
        censored_symbols=len(source.coverage) - complete,
        recommendations=recommendations,
        completed_trades=trades,
    )
    _write_gate(output, gate)
    rprint(
        f"[green]완료[/green] {strategy.value} · 입력 {len(source.contexts)} · "
        + f"완전 {complete} · 검열 {len(source.coverage) - complete} · 거래 {trades} · {output}"
    )


def _fail(
    output: Path,
    strategy: StrategyMode,
    reasons: tuple[str, ...],
    session_date: dt.date | None = None,
) -> NoReturn:
    gate = ChallengerReplayGate(
        strategy=strategy.value,
        passed=False,
        session_date=session_date,
        reasons=reasons,
    )
    _write_gate(output, gate)
    rprint(f"[red]거부[/red] {', '.join(reasons)} · {output}")
    raise typer.Exit(code=2)


def _write_gate(output: Path, gate: ChallengerReplayGate) -> None:
    output.mkdir(parents=True, exist_ok=True)
    _ = (output / "challenger_replay_gate.json").write_text(
        gate.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    status = "통과" if gate.passed else "거부"
    lines = [
        "# Challenger 장마감 causal replay 게이트",
        "",
        "> 확정 수익이 아닌 paper 전진검증용 shadow 연구 결과입니다.",
        "",
        f"- 상태: {status}",
        f"- 전략: {gate.strategy}",
        f"- 세션: {gate.session_date or '확인 불가'}",
        f"- 입력 스냅샷: {gate.input_snapshots}",
        f"- 완전/검열 종목: {gate.complete_symbols}/{gate.censored_symbols}",
        f"- 추천/완료 거래: {gate.recommendations}/{gate.completed_trades}",
        f"- 사유: {', '.join(gate.reasons) if gate.reasons else '없음'}",
        "- ORB와 동일 포트폴리오 위험 비교가 구현되기 전에는 승격 비교 대상이 아닙니다.",
    ]
    _ = (output / "challenger_replay_gate_ko.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    typer.run(main)

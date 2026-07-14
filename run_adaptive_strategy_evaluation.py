#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic", "rich", "typer"]
# ///
# ─── How to run ───
# ./run_adaptive_strategy_evaluation.py outputs/live_sessions/<session>

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich import print as rprint

from trading_agent.adaptive_evaluation import evaluate_strategy
from trading_agent.adaptive_evaluation_report import write_adaptive_evaluation
from trading_agent.adaptive_evaluation_source import AdaptiveSourceError, load_evaluation_source


def main(
    session_dir: str,
    output_dir: Annotated[str | None, typer.Option("--output-dir")] = None,
) -> None:
    session = Path(session_dir)
    try:
        source = load_evaluation_source(session)
    except AdaptiveSourceError as error:
        typer.echo(f"오류: {error}", err=True)
        raise typer.Exit(code=2) from error
    result = evaluate_strategy(source.sessions, source.context)
    output = Path(output_dir) if output_dir is not None else session / "adaptive_evaluation"
    assignments = tuple(feature for session_row in source.sessions for feature in session_row.features)
    write_adaptive_evaluation(output, result, assignments)
    rprint(
        f"[green]적응형 평가 완료[/green] action={result.action.value}, "
        + f"eligible_days={result.windows[-1].observed_sessions}, "
        + f"trades={result.windows[-1].trade_count}, {output}"
    )


if __name__ == "__main__":
    typer.run(main)

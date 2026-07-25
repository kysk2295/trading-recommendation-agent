#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "rich>=13.9", "typer>=0.15"]
# ///

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Annotated

import httpx2
import typer
from rich import print as rprint

from trading_agent.dashboard_snapshot import (
    DashboardCredentialError,
    JobRow,
    collect_dashboard_snapshot,
    load_dashboard_credentials,
)

app = typer.Typer(help="로컬 산출물을 redacted 운영 snapshot으로 안전하게 전송합니다.")
DEFAULT_OUTPUTS = Path(__file__).resolve().parent / "outputs"
DEFAULT_CREDENTIALS = Path.home() / ".config" / "trading-agent" / "dashboard.env"


@app.command(help="로컬 산출물을 redacted 운영 snapshot으로 안전하게 전송합니다.")
def publish(
    outputs: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_OUTPUTS,
    credentials: Annotated[Path, typer.Option()] = DEFAULT_CREDENTIALS,
    interval_seconds: Annotated[int, typer.Option(min=5, max=300)] = 15,
    once: Annotated[bool, typer.Option(help="한 번 전송한 뒤 종료")] = False,
    dry_run: Annotated[bool, typer.Option(help="외부 전송 없이 snapshot 경계만 검증")] = False,
) -> None:
    try:
        config = load_dashboard_credentials(credentials)
    except DashboardCredentialError as error:
        raise typer.BadParameter(str(error), param_hint="--credentials") from error
    while True:
        snapshot = collect_dashboard_snapshot(outputs, jobs=_launchd_jobs())
        if dry_run:
            rprint(
                "[green]dashboard snapshot valid[/green] "
                f"session={snapshot.forward.session_date} "
                f"recommendations={len(snapshot.recommendations)}"
            )
        else:
            _publish_snapshot(
                config.dashboard_url,
                config.ingest_token.get_secret_value(),
                snapshot.model_dump(mode="json"),
            )
            rprint(
                "[green]dashboard snapshot published[/green] "
                f"generated_at={snapshot.generated_at.isoformat()}"
            )
        if once:
            return
        time.sleep(interval_seconds)


def _publish_snapshot(url: str, token: str, payload: dict[str, object]) -> None:
    try:
        with httpx2.Client(
            timeout=httpx2.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = client.post(
                f"{url}/api/ingest",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            response.raise_for_status()
    except httpx2.HTTPError as error:
        rprint("[red]dashboard publish failed[/red]")
        raise typer.Exit(code=1) from error


def _launchd_jobs() -> tuple[JobRow, ...]:
    try:
        result = subprocess.run(
            ("launchctl", "list"),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    rows: list[JobRow] = []
    for line in result.stdout.splitlines()[1:]:
        columns = line.split("\t")
        if len(columns) != 3 or not columns[2].startswith("ai.trading-agent."):
            continue
        pid = None if columns[0] == "-" else int(columns[0])
        try:
            exit_code = int(columns[1])
        except ValueError:
            continue
        rows.append((columns[2], pid, exit_code))
    return tuple(rows)


if __name__ == "__main__":
    app()

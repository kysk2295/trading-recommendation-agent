#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "httpx2[http2,brotli,zstd]",
#     "pydantic>=2.11",
#     "typer>=0.15",
# ]
# ///
#
# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run run_kr_same_cycle_source_readiness.py --help
# 3. Or make executable and run:
#      chmod +x run_kr_same_cycle_source_readiness.py
#      ./run_kr_same_cycle_source_readiness.py --help
# ──────────────────

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final

import typer

from scr_backtest.kis_intraday import MissingKisCredentialsError
from trading_agent.kis_auth import (
    KisMode,
    UnsafeSecretFileError,
    load_kis_credentials,
)
from trading_agent.ls_config import (
    InvalidLsCredentialsError,
    LsSecretEncodingError,
    LsSecretFileError,
    load_ls_credentials,
)
from trading_agent.opendart_config import (
    InvalidOpenDartCredentialsError,
    OpenDartSecretEncodingError,
    OpenDartSecretFileError,
    load_opendart_credentials,
)
from trading_agent.private_report import write_private_report

DEFAULT_SECRETS_ROOT: Final = Path.home() / ".config/trading-agent"
DEFAULT_OUTPUT_DIR: Final = Path("outputs/kr_theme/source_readiness/latest")
REPORT_NAME: Final = "kr_same_cycle_source_readiness_ko.md"


class KrSourceName(StrEnum):
    OPENDART = "opendart"
    LS_NWS = "ls_nws"
    KIS_LIVE = "kis_live"


class KrSourceReadinessState(StrEnum):
    READY = "ready"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class KrSourceReadiness:
    source: KrSourceName
    state: KrSourceReadinessState


def collect_kr_same_cycle_source_readiness(
    secrets_root: Path,
) -> tuple[KrSourceReadiness, ...]:
    """Validate the three credential boundaries without opening a network client."""
    return (
        _opendart_readiness(secrets_root / "opendart.env"),
        _ls_readiness(secrets_root / "ls.env"),
        _kis_readiness(secrets_root / "kis.env"),
    )


def main(
    secrets_root: Annotated[Path, typer.Option()] = DEFAULT_SECRETS_ROOT,
    output_dir: Annotated[Path, typer.Option()] = DEFAULT_OUTPUT_DIR,
) -> None:
    """Write a secret-free readiness report for the exact four-source KR cycle."""
    readiness = collect_kr_same_cycle_source_readiness(secrets_root)
    ready = all(item.state is KrSourceReadinessState.READY for item in readiness)
    result = "ready" if ready else "blocked"
    write_private_report(
        output_dir / REPORT_NAME,
        "\n".join(
            (
                "# KR same-cycle source credential readiness",
                "",
                "> 비밀값을 출력하거나 provider network를 열지 않는 pre-open 진단입니다.",
                "",
                f"- result: {result}",
                *(f"- {item.source.value}: {item.state.value}" for item in readiness),
                "- required terminal sources: 4",
                "- credential sources: 3",
                "- network requests: 0",
                "- account/order mutation: 0",
                "",
            )
        ),
    )
    typer.echo(f"KR same-cycle source readiness result={result}")
    if not ready:
        raise typer.Exit(code=1)


def _opendart_readiness(path: Path) -> KrSourceReadiness:
    try:
        _ = load_opendart_credentials(path)
    except (
        InvalidOpenDartCredentialsError,
        OpenDartSecretEncodingError,
        OpenDartSecretFileError,
    ):
        return KrSourceReadiness(
            KrSourceName.OPENDART,
            KrSourceReadinessState.UNAVAILABLE,
        )
    return KrSourceReadiness(KrSourceName.OPENDART, KrSourceReadinessState.READY)


def _ls_readiness(path: Path) -> KrSourceReadiness:
    try:
        _ = load_ls_credentials(path)
    except (
        InvalidLsCredentialsError,
        LsSecretEncodingError,
        LsSecretFileError,
    ):
        return KrSourceReadiness(
            KrSourceName.LS_NWS,
            KrSourceReadinessState.UNAVAILABLE,
        )
    return KrSourceReadiness(KrSourceName.LS_NWS, KrSourceReadinessState.READY)


def _kis_readiness(path: Path) -> KrSourceReadiness:
    try:
        _ = load_kis_credentials(KisMode.LIVE, path)
    except (
        MissingKisCredentialsError,
        OSError,
        UnicodeError,
        UnsafeSecretFileError,
    ):
        return KrSourceReadiness(
            KrSourceName.KIS_LIVE,
            KrSourceReadinessState.UNAVAILABLE,
        )
    return KrSourceReadiness(KrSourceName.KIS_LIVE, KrSourceReadinessState.READY)


if __name__ == "__main__":
    typer.run(main)

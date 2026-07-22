from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import typer

from trading_agent.strategy_factory import StrategyMode


@dataclass(frozen=True, slots=True)
class ResearchProjectionWatchConfig:
    projection_store: Path
    canonical_root: Path
    security_master_store: Path


@dataclass(frozen=True, slots=True)
class WatchScanConfig:
    strategy: StrategyMode
    top: int
    max_pages: int
    research_projection: ResearchProjectionWatchConfig | None = None
    delivery_database: Path | None = None


def research_projection_watch_config(
    projection_store: Path | None,
    canonical_root: Path | None,
    security_master_store: Path | None,
) -> ResearchProjectionWatchConfig | None:
    paths = (projection_store, canonical_root, security_master_store)
    if all(path is None for path in paths):
        return None
    if any(path is None for path in paths):
        raise typer.BadParameter("research projection 경로 세 개는 모두 함께 지정해야 합니다")
    assert projection_store is not None
    assert canonical_root is not None
    assert security_master_store is not None
    return ResearchProjectionWatchConfig(
        projection_store,
        canonical_root,
        security_master_store,
    )


def scan_command(output: Path, config: WatchScanConfig) -> tuple[str, ...]:
    command = (
        str(Path(__file__).resolve().parents[1] / "run_kis_paper_scan.py"),
        "--output-dir",
        str(output),
        "--strategy",
        config.strategy.value,
        "--top",
        str(config.top),
        "--max-pages",
        str(config.max_pages),
    )
    research = config.research_projection
    if research is not None:
        command = (
            *command,
            "--research-projection-store",
            str(research.projection_store),
            "--research-canonical-root",
            str(research.canonical_root),
            "--research-security-master-store",
            str(research.security_master_store),
        )
    if config.delivery_database is not None:
        command = (
            *command,
            "--delivery-database",
            str(config.delivery_database),
        )
    return command


__all__ = (
    "ResearchProjectionWatchConfig",
    "WatchScanConfig",
    "research_projection_watch_config",
    "scan_command",
)

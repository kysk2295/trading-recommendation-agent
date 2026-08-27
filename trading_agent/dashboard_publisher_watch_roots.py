from __future__ import annotations

from pathlib import Path

from trading_agent.kr_autonomous_operator_paths import KrAutonomousOperatorPaths


def watch_roots(
    outputs: Path,
    *,
    kr_day_state_root: Path | None = None,
    kr_operator_paths: KrAutonomousOperatorPaths | None = None,
) -> tuple[Path, ...]:
    root = outputs.resolve()
    candidates = tuple(
        root / name
        for name in (
            "live_sessions",
            "source_evidence",
            "experiment_control",
            "lane_control",
            "kr_theme",
            "derivatives",
            "paper",
            "hermes",
            "system",
        )
    )
    operational = () if kr_day_state_root is None else _narrow_existing_root(kr_day_state_root)
    operator = () if kr_operator_paths is None else _narrow_existing_root(kr_operator_paths.task_database.parent)
    existing = tuple(dict.fromkeys(path for path in (*candidates, *operational, *operator) if path.is_dir()))
    return existing or (root,)


def _narrow_existing_root(path: Path) -> tuple[Path, ...]:
    requested = path.resolve()
    if requested.is_dir():
        return (requested,)
    parent = requested.parent
    if parent.is_dir() and parent not in {Path.home().resolve(), Path(requested.anchor)}:
        return (parent,)
    return ()


__all__ = ("watch_roots",)

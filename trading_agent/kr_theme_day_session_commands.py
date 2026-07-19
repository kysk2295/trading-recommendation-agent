from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import assert_never

from trading_agent.kr_theme_day_session_audit import KrThemeDaySessionPhase
from trading_agent.kr_theme_day_session_manifest import KrThemeDaySessionManifest
from trading_agent.kr_theme_day_trial import kr_theme_day_trial_id

_ROOT = Path(__file__).resolve().parent.parent


def kr_theme_day_session_child_command(
    manifest: KrThemeDaySessionManifest,
    phase: KrThemeDaySessionPhase,
    observed_at: dt.datetime,
) -> tuple[str, ...]:
    output = manifest.paths.output_root / phase.value / _path_time(observed_at)
    trial_id = kr_theme_day_trial_id(manifest.session_date, manifest.strategy_version)
    match phase:
        case KrThemeDaySessionPhase.REGISTER:
            return (
                str(_ROOT / "run_kr_theme_day_trial.py"),
                "register",
                "--strategy-version",
                manifest.strategy_version,
                "--code-version",
                manifest.code_version,
                "--opportunity-strategy-version",
                manifest.opportunity_strategy_version,
                "--session-date",
                manifest.session_date.isoformat(),
                "--registered-at",
                manifest.registered_at.isoformat(),
                "--calendar-store",
                str(manifest.paths.calendar_store),
                "--database",
                str(manifest.paths.experiment_ledger),
                "--output-dir",
                str(output),
            )
        case KrThemeDaySessionPhase.START:
            return (
                str(_ROOT / "run_kr_theme_day_trial.py"),
                "start",
                "--trial-id",
                trial_id,
                "--occurred-at",
                _session_open(manifest).isoformat(),
                "--database",
                str(manifest.paths.experiment_ledger),
                "--output-dir",
                str(output),
            )
        case KrThemeDaySessionPhase.INTRADAY_COLLECT:
            return _collection_command(manifest, output, eod=False)
        case KrThemeDaySessionPhase.INTRADAY_ENTRY:
            return (
                str(_ROOT / "run_kr_theme_day_intraday.py"),
                "--opportunity-outbox",
                str(manifest.paths.opportunity_outbox),
                "--opportunity-id",
                manifest.opportunity_id,
                "--opportunity-sha256",
                manifest.opportunity_sha256,
                "--strategy-version",
                manifest.strategy_version,
                "--evaluated-at",
                observed_at.isoformat(),
                "--filled-at",
                observed_at.isoformat(),
                "--database",
                str(manifest.paths.experiment_ledger),
                "--receipt-store",
                str(manifest.paths.receipt_store),
                "--entry-store",
                str(manifest.paths.entry_store),
                "--output-dir",
                str(output),
            )
        case KrThemeDaySessionPhase.INTRADAY_EXIT | KrThemeDaySessionPhase.EOD_EXIT:
            return (
                str(_ROOT / "run_kr_theme_day_shadow_exit.py"),
                "--trial-id",
                trial_id,
                "--evaluated-at",
                observed_at.isoformat(),
                "--receipt-store",
                str(manifest.paths.receipt_store),
                "--entry-store",
                str(manifest.paths.entry_store),
                "--exit-store",
                str(manifest.paths.exit_store),
                "--output-dir",
                str(output),
            )
        case KrThemeDaySessionPhase.EOD_COLLECT:
            return _collection_command(manifest, output, eod=True)
        case KrThemeDaySessionPhase.POST_SESSION:
            return (
                str(_ROOT / "run_kr_theme_day_post_session.py"),
                "--experiment-ledger",
                str(manifest.paths.experiment_ledger),
                "--entry-store",
                str(manifest.paths.entry_store),
                "--exit-store",
                str(manifest.paths.exit_store),
                "--terminal-store",
                str(manifest.paths.terminal_store),
                "--review-store",
                str(manifest.paths.review_store),
                "--calendar-store",
                str(manifest.paths.calendar_store),
                "--trial-id",
                trial_id,
                "--strategy-version",
                manifest.strategy_version,
                "--session-date",
                manifest.session_date.isoformat(),
                "--output-dir",
                str(output),
            )
        case unreachable:
            assert_never(unreachable)


def _collection_command(
    manifest: KrThemeDaySessionManifest,
    output: Path,
    *,
    eod: bool,
) -> tuple[str, ...]:
    base = (
        str(_ROOT / "run_kis_kr_market_collect.py"),
        "--symbol",
        manifest.symbol,
        "--calendar-store",
        str(manifest.paths.calendar_store),
        "--calendar-snapshot-id",
        manifest.calendar_snapshot_id,
        "--receipt-store",
        str(manifest.paths.receipt_store),
        "--output-dir",
        str(output),
    )
    fixture = manifest.paths.eod_fixture_manifest if eod else manifest.paths.intraday_fixture_manifest
    fixture_arguments = () if fixture is None else ("--fixture-manifest", str(fixture))
    eod_argument = ("--eod-minute",) if eod else ()
    return (*base, *fixture_arguments, *eod_argument)


def _session_open(manifest: KrThemeDaySessionManifest) -> dt.datetime:
    return dt.datetime.combine(
        manifest.session_date,
        dt.time(9),
        tzinfo=dt.timezone(dt.timedelta(hours=9)),
    )


def _path_time(value: dt.datetime) -> str:
    return value.isoformat().replace(":", "").replace("+", "p")

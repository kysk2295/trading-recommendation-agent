from __future__ import annotations

import datetime as dt
import stat
from dataclasses import dataclass
from pathlib import Path

from trading_agent.future_session_kr_supervisor_models import InvalidKrFutureSessionSupervisorError
from trading_agent.future_session_plan_models import FutureSessionPlanRequest
from trading_agent.kr_theme_day_trial import kr_theme_day_trial_id
from trading_agent.kr_theme_research_chain_rollover import KrThemeResearchRolloverBundle
from trading_agent.signal_contract_models import OpportunitySnapshot


@dataclass(frozen=True, slots=True)
class KrSupervisorPaths:
    root: Path
    calendar: Path
    source: Path
    collection: Path
    runs: Path
    projection: Path
    operator: Path
    outbox: Path
    day: Path

    @classmethod
    def from_root(cls, root: Path) -> KrSupervisorPaths:
        session = root / "session"
        projection = session / "projection"
        return cls(
            root=session,
            calendar=session / "calendar.sqlite3",
            source=session / "kr_theme.sqlite3",
            collection=session / "same_cycle",
            runs=session / "runs",
            projection=projection,
            operator=session / "operator",
            outbox=projection / "opportunities.v1.jsonl",
            day=session / "day",
        )


def kr_supervisor_commands(
    request: FutureSessionPlanRequest,
    bundle: KrThemeResearchRolloverBundle,
    paths: KrSupervisorPaths,
    session_date: dt.date,
) -> dict[str, tuple[str, ...]]:
    if request.kr_rollover_bundle is None or request.delivery_database is None:
        raise InvalidKrFutureSessionSupervisorError
    runtime = request.frozen_runtime.directory
    day_version = bundle.day_version.strategy_version
    opportunity_version = bundle.opportunity_version.strategy_version
    date = session_date.isoformat()
    registered_at = dt.datetime.combine(
        session_date,
        dt.time(8, 55),
        tzinfo=dt.timezone(dt.timedelta(hours=9)),
    ).isoformat()
    trial_id = kr_theme_day_trial_id(session_date, day_version)
    cycle_id = f"kr-future-{session_date:%Y%m%d}-{request.frozen_runtime.commit_sha[:12]}"
    control = paths.root / "control"
    return {
        "calendar": (
            str(runtime / "run_kis_kr_session_calendar_collect.py"),
            "--calendar-store",
            str(paths.calendar),
            "--output-dir",
            str(paths.root / "calendar-report"),
        ),
        "composite": (
            str(runtime / "run_kr_theme_day_composite.py"),
            "--day-strategy-version",
            day_version,
            "--opportunity-strategy-version",
            opportunity_version,
            "--registered-at",
            registered_at,
            "--database",
            str(request.experiment_ledger),
            "--output-dir",
            str(control / "composite"),
        ),
        "register": (
            str(runtime / "run_kr_theme_day_trial.py"),
            "register",
            "--strategy-version",
            day_version,
            "--code-version",
            request.frozen_runtime.commit_sha,
            "--opportunity-strategy-version",
            opportunity_version,
            "--session-date",
            date,
            "--registered-at",
            registered_at,
            "--calendar-store",
            str(paths.calendar),
            "--database",
            str(request.experiment_ledger),
            "--output-dir",
            str(control / "trial"),
        ),
        "start": (
            str(runtime / "run_kr_theme_day_trial.py"),
            "start",
            "--trial-id",
            trial_id,
            "--occurred-at",
            f"{date}T09:00:00+09:00",
            "--database",
            str(request.experiment_ledger),
            "--output-dir",
            str(control / "start"),
        ),
        "cycle": (
            str(runtime / "run_kr_same_cycle_opportunity.py"),
            "--collection-cycle-id",
            cycle_id,
            "--collection-date",
            date,
            "--policy",
            str(request.kr_rollover_bundle.parent / "opportunity-policy.json"),
            "--database",
            str(paths.source),
            "--experiment-ledger",
            str(request.experiment_ledger),
            "--delivery-database",
            str(request.delivery_database),
            "--collection-output-dir",
            str(paths.collection),
            "--run-root",
            str(paths.runs),
            "--projection-output-dir",
            str(paths.projection),
            "--output-dir",
            str(paths.operator),
        ),
        "post": (
            str(runtime / "run_kr_theme_day_post_session.py"),
            "--experiment-ledger",
            str(request.experiment_ledger),
            "--entry-store",
            str(paths.day / "entries.sqlite3"),
            "--exit-store",
            str(paths.day / "exits.sqlite3"),
            "--terminal-store",
            str(paths.day / "terminals.sqlite3"),
            "--delivery-store",
            str(request.delivery_database),
            "--review-store",
            str(paths.day / "reviews.sqlite3"),
            "--calendar-store",
            str(paths.calendar),
            "--trial-id",
            trial_id,
            "--strategy-version",
            day_version,
            "--session-date",
            date,
            "--output-dir",
            str(paths.day / "post-session"),
        ),
    }


def kr_supervisor_opportunity_commands(
    request: FutureSessionPlanRequest,
    bundle: KrThemeResearchRolloverBundle,
    paths: KrSupervisorPaths,
    session_date: dt.date,
    opportunity_id: str,
) -> dict[str, tuple[str, ...]]:
    if request.delivery_database is None:
        raise InvalidKrFutureSessionSupervisorError
    runtime = request.frozen_runtime.directory
    manifest = paths.day / "session.json"
    day_version = bundle.day_version.strategy_version
    trial_id = kr_theme_day_trial_id(session_date, day_version)
    common = (
        "--manifest",
        str(manifest),
        "--trial-id",
        trial_id,
        "--opportunity-id",
        opportunity_id,
        "--output-dir",
        str(paths.day / "onboard"),
        "--experiment-ledger",
        str(request.experiment_ledger),
        "--calendar-store",
        str(paths.calendar),
        "--opportunity-outbox",
        str(paths.outbox),
        "--receipt-store",
        str(paths.day / "market-receipts.sqlite3"),
        "--entry-store",
        str(paths.day / "entries.sqlite3"),
        "--delivery-database",
        str(request.delivery_database),
        "--exit-store",
        str(paths.day / "exits.sqlite3"),
        "--terminal-store",
        str(paths.day / "terminals.sqlite3"),
        "--review-store",
        str(paths.day / "reviews.sqlite3"),
        "--audit-store",
        str(paths.day / "session-audit.sqlite3"),
        "--output-root",
        str(paths.day / "runtime"),
    )
    return {
        "onboard": (str(runtime / "run_kr_theme_day_session.py"), "onboard", *common),
        "tick": (
            str(runtime / "run_kr_theme_day_session.py"),
            "tick",
            "--manifest",
            str(manifest),
            "--output-dir",
            str(paths.day / "tick-report"),
        ),
        "verify": (
            str(runtime / "run_kr_theme_day_session_verify.py"),
            "--manifest",
            str(manifest),
            "--output-dir",
            str(paths.day / "verify"),
        ),
    }


def cycle_opportunities(path: Path, cycle_id: str) -> tuple[OpportunitySnapshot, ...]:
    if not path.is_file() or path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        return ()
    values = tuple(
        OpportunitySnapshot.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line
    )
    return tuple(
        value
        for value in values
        if any(ref.namespace == "kr/collection_cycle" and ref.record_id == cycle_id for ref in value.evidence_refs)
    )


def require_read_only_commands(commands: dict[str, tuple[str, ...]]) -> None:
    rendered = "\n".join(" ".join(command) for command in commands.values())
    banned = (
        "/stock/accno",
        "/stock/order",
        "paper-api.alpaca.markets",
        "trtype=1",
        "trtype=2",
    )
    if any(value in rendered for value in banned):
        raise InvalidKrFutureSessionSupervisorError


__all__ = (
    "KrSupervisorPaths",
    "cycle_opportunities",
    "kr_supervisor_commands",
    "kr_supervisor_opportunity_commands",
    "require_read_only_commands",
)

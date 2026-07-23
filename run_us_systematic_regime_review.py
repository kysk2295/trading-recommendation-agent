#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.private_report import write_private_report
from trading_agent.swing_shadow_cli_files import (
    load_private_swing_sources,
)
from trading_agent.systematic_regime_review_store import SystematicRegimeReviewStore
from trading_agent.systematic_regime_reviewer import review_systematic_regime_trial
from trading_agent.systematic_regime_store import SystematicRegimeStore

REPORT_NAME = "us_systematic_regime_review_ko.md"


class UsSystematicRegimeReviewCliError(ValueError):
    @override
    def __str__(self) -> str:
        return "US systematic regime review CLI is invalid"


@dataclass(frozen=True, slots=True)
class _ReviewSummary:
    eligible_trials: int
    reviews_created: int
    reviews_replayed: int


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="US systematic regime terminal trial을 authority 없이 독립 검토",
    )
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--systematic-database", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--review-ledger", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--card-id")
    selection.add_argument("--all-terminal", action="store_true")
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    now: dt.datetime | None = None,
) -> int:
    args = parse_args(argv)
    timestamp = dt.datetime.now(dt.UTC) if now is None else now
    output = args.output_dir.expanduser().resolve(strict=False)
    try:
        summary = _execute(args, timestamp)
    except (OSError, RuntimeError, ValueError):
        _write_report(output, None)
        return 1
    _write_report(output, summary)
    return 0


def _execute(args: argparse.Namespace, timestamp: dt.datetime) -> _ReviewSummary:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise UsSystematicRegimeReviewCliError
    experiment = ExperimentLedgerReader(
        args.experiment_ledger.expanduser().resolve(strict=False),
    )
    systematic = SystematicRegimeStore(
        args.systematic_database.expanduser().resolve(strict=False),
    )
    sources = load_private_swing_sources(
        args.source_root.expanduser().resolve(strict=False),
    )
    reviews = SystematicRegimeReviewStore(
        args.review_ledger.expanduser().resolve(strict=False),
    )
    targets = (experiment.path, systematic.path, reviews.path)
    report = args.output_dir.expanduser().resolve(strict=False) / REPORT_NAME
    if (
        not experiment.is_initialized()
        or not systematic.path.is_file()
        or len(set(targets)) != len(targets)
        or report in targets
    ):
        raise UsSystematicRegimeReviewCliError
    card_ids = (
        (args.card_id,)
        if args.card_id is not None
        else _terminal_card_ids(experiment, systematic)
    )
    created = 0
    for card_id in card_ids:
        result = review_systematic_regime_trial(
            experiment_ledger=experiment,
            systematic_store=systematic,
            daily_sources=sources,
            reviews=reviews,
            card_id=card_id,
            reviewed_at=timestamp,
        )
        created += int(result.created)
    return _ReviewSummary(
        eligible_trials=len(card_ids),
        reviews_created=created,
        reviews_replayed=len(card_ids) - created,
    )


def _terminal_card_ids(
    experiment: ExperimentLedgerReader,
    systematic: SystematicRegimeStore,
) -> tuple[str, ...]:
    cards = (*systematic.cards(), *systematic.expired_cards())
    if len(cards) != len({card.card_id for card in cards}):
        raise UsSystematicRegimeReviewCliError
    terminal: list[str] = []
    for card in cards:
        trials = tuple(
            stored.registration
            for stored in experiment.multi_market_trials()
            if stored.registration.trial_id.startswith(
                f"us-systematic-regime-{card.target_session:%Y%m%d}-"
            )
            and stored.registration.strategy_version == card.strategy_version
        )
        if len(trials) != 1:
            raise UsSystematicRegimeReviewCliError
        events = experiment.multi_market_trial_events(trials[0].trial_id)
        if events and events[-1].event.event_kind in (
            TrialEventKind.COMPLETED,
            TrialEventKind.CENSORED,
        ):
            terminal.append(card.card_id)
    return tuple(sorted(terminal))


def _write_report(output: Path, summary: _ReviewSummary | None) -> None:
    details = (
        (
            "result: blocked_source",
            "external broker mutation: 0",
            "automatic state change: false",
            "allocation change: false",
        )
        if summary is None
        else (
            "result: completed",
            f"eligible_trials: {summary.eligible_trials}",
            f"reviews_created: {summary.reviews_created}",
            f"reviews_replayed: {summary.reviews_replayed}",
            "external broker mutation: 0",
            "automatic state change: false",
            "allocation change: false",
        )
    )
    write_private_report(
        output / REPORT_NAME,
        "\n".join(
            (
                "# US Systematic Regime Independent Reviewer",
                "",
                "> exact local terminal evidence만 읽고 권한을 변경하지 않습니다.",
                "",
                *(f"- {detail}" for detail in details),
                "",
            )
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import datetime as dt
import subprocess
from decimal import Decimal
from pathlib import Path

import run_kr_day_close_report as cli
from tests.kr_day_shadow_support import run_authorized_kr_shadow_tick as run_kr_day_capsule_shadow_tick
from tests.test_kr_day_capsule_shadow import _advance, _entry_evaluation, _plain_evaluation, _rebuild
from tests.test_kr_day_market_close_report import _outcome, _request
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluation
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.private_immutable_file import publish_private_immutable_text

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_kr_day_close_report.py"


def test_cli_help_exposes_only_local_query_and_output_paths() -> None:
    # Given: the real KR close-report CLI.
    command = ("uv", "run", "python", str(SCRIPT), "--help")

    # When: its public parser surface is rendered.
    completed = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)

    # Then: only local evidence/store/output controls are exposed.
    assert completed.returncode == 0
    assert {"--finalization", "--shadow-store", "--report-root", "--policy-root"} <= set(
        word.rstrip(",") for word in completed.stdout.split()
    )
    assert not any(
        token in completed.stdout.lower()
        for token in (
            "--credential",
            "--endpoint",
            "--account",
            "--balance",
            "--position",
            "--order",
            "--arm",
            "--broker",
            "--force",
            "--paper",
        )
    )


def test_cli_publishes_read_only_report_policy_and_replay_dedupes(tmp_path: Path) -> None:
    # Given: a private finalized request linked to the exact local Shadow store.
    finalization, store = _fixture(tmp_path)
    command = _command(finalization, store, tmp_path / "reports", tmp_path / "policies")

    # When: the close is run twice through the real process boundary.
    first = _run(command)
    replay = _run(command)

    # Then: report and policy are immutable, deduplicated, and explicitly non-profit KR research.
    first_payload = cli._CliResult.model_validate_json(first.stdout)
    replay_payload = cli._CliResult.model_validate_json(replay.stdout)
    assert (first.returncode, replay.returncode) == (0, 0)
    assert first_payload.report_created is True
    assert first_payload.metrics_created is True
    assert first_payload.policy_created is True
    assert replay_payload.report_created is False
    assert replay_payload.metrics_created is False
    assert replay_payload.policy_created is False
    assert replay_payload.report_id == first_payload.report_id
    assert replay_payload.metrics_id == first_payload.metrics_id
    assert replay_payload.policy_id == first_payload.policy_id
    assert first_payload.provider_read_only is True
    assert first_payload.actual_return is None
    assert first_payload.profitability_claim is False
    assert first_payload.effective_session_date == "2026-08-26"
    assert first_payload.cumulative_modeled_return == first_payload.modeled_return
    assert first_payload.failed_count == 0
    assert first_payload.censored_count == 0


def test_cli_finalizes_no_signal_and_blocked_metrics_with_replay_dedupe(tmp_path: Path) -> None:
    # Given: private official-close inputs for no-signal and blocked Shadow outcomes.
    no_signal_finalization, no_signal_store = _single_event_fixture(
        tmp_path / "no-signal",
        _plain_evaluation(),
    )
    entry = _entry_evaluation()
    blocked_finalization, blocked_store = _single_event_fixture(
        tmp_path / "blocked",
        _rebuild(entry, evaluated_at=entry.evaluated_at + dt.timedelta(seconds=10)),
    )
    no_signal_command = _command(
        no_signal_finalization,
        no_signal_store,
        tmp_path / "no-signal" / "reports",
        tmp_path / "no-signal" / "policies",
    )
    blocked_command = _command(
        blocked_finalization,
        blocked_store,
        tmp_path / "blocked" / "reports",
        tmp_path / "blocked" / "policies",
    )

    # When: each request crosses the real CLI boundary and no-signal is replayed.
    first = _run(no_signal_command)
    replay = _run(no_signal_command)
    blocked = _run(blocked_command)

    # Then: both outcomes are counted and the repeated no-signal request is a no-op.
    first_payload = cli._CliResult.model_validate_json(first.stdout)
    replay_payload = cli._CliResult.model_validate_json(replay.stdout)
    blocked_payload = cli._CliResult.model_validate_json(blocked.stdout)
    assert (first.returncode, replay.returncode, blocked.returncode) == (0, 0, 0)
    assert (first_payload.no_signal_count, first_payload.blocked_count) == (1, 0)
    assert (blocked_payload.no_signal_count, blocked_payload.blocked_count) == (0, 1)
    assert replay_payload.report_id == first_payload.report_id
    assert replay_payload.metrics_id == first_payload.metrics_id
    assert (replay_payload.report_created, replay_payload.metrics_created) == (False, False)


def test_cli_bad_private_input_exits_without_traceback_or_publication(tmp_path: Path) -> None:
    # Given: a malformed private finalization artifact and valid local store path.
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    invalid.chmod(0o600)

    # When: it is submitted through the real CLI.
    completed = _run(_command(invalid, tmp_path / "missing.sqlite3", tmp_path / "reports", tmp_path / "policies"))

    # Then: the boundary blocks without traceback or output mutation.
    payload = cli._CliResult.model_validate_json(completed.stdout)
    assert completed.returncode == 2
    assert payload.result == "blocked"
    assert "Traceback" not in completed.stderr
    assert not (tmp_path / "reports").exists()
    assert not (tmp_path / "policies").exists()


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    store_path = tmp_path / "shadow" / "events.sqlite3"
    store = KrDayCapsuleShadowStore(store_path)
    entry = _entry_evaluation()
    stopped = _advance(entry, low=Decimal("9900"), high=Decimal("10400"))
    _ = run_kr_day_capsule_shadow_tick(store, (entry,))
    _ = run_kr_day_capsule_shadow_tick(store, (stopped,))
    events = store.events()
    finalization = tmp_path / "finalization.json"
    assert publish_private_immutable_text(
        finalization,
        canonical_experiment_ledger_json(_request(events, (_outcome(events),))) + "\n",
    )
    return finalization, store_path


def _single_event_fixture(
    tmp_path: Path,
    evaluation: KrDayCapsuleEvaluation,
) -> tuple[Path, Path]:
    store_path = tmp_path / "shadow" / "events.sqlite3"
    store = KrDayCapsuleShadowStore(store_path)
    _ = run_kr_day_capsule_shadow_tick(store, (evaluation,))
    events = store.events()
    finalization = tmp_path / "finalization.json"
    assert publish_private_immutable_text(
        finalization,
        canonical_experiment_ledger_json(_request(events, (_outcome(events),))) + "\n",
    )
    return finalization, store_path


def _command(finalization: Path, store: Path, reports: Path, policies: Path) -> tuple[str, ...]:
    return (
        "uv",
        "run",
        "python",
        str(SCRIPT),
        "--finalization",
        str(finalization),
        "--shadow-store",
        str(store),
        "--report-root",
        str(reports),
        "--policy-root",
        str(policies),
    )


def _run(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)

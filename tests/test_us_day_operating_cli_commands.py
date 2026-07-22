from __future__ import annotations

import json
from pathlib import Path

from tests.test_run_us_day_operating_session import CapturingRunnerFactory, StaticRunner
from tests.test_us_day_acceptance_evidence import _clean_repository, _git, _terminal
from tests.us_day_operating_fixtures import AT, admission, readiness
from trading_agent.models import Recommendation, RecommendationState
from trading_agent.paper_operating_session_models import PaperOrderAdmissionRequest
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.store import PaperStore
from trading_agent.us_day_acceptance_evidence import UsDaySessionTerminal
from trading_agent.us_day_operating_cli import UsDayCliDependencies, main, parser
from trading_agent.us_day_operating_models import (
    UsDayOperatingResult,
    UsDayOperatingStatus,
    UsDayOperatingTransition,
)
from trading_agent.us_day_session_inspection import UsDayPreflightInspection, UsDaySessionInspection


class StaticReadOnlyOperations:
    __slots__ = ("inspection",)

    def __init__(self, inspection: UsDaySessionInspection) -> None:
        self.inspection = inspection

    def preflight(
        self,
        execution_store: Path,
        request: PaperOrderAdmissionRequest,
    ) -> UsDayPreflightInspection:
        return UsDayPreflightInspection(self.inspection, True, ())

    def recover(self, execution_store: Path) -> UsDaySessionInspection:
        return self.inspection


def test_cli_help_exposes_complete_operating_command_set() -> None:
    help_text = parser().format_help()

    assert all(command in help_text for command in ("preflight", "run", "recover", "finalize", "evidence"))


def test_preflight_and_recover_use_read_only_operation_surface(tmp_path: Path, capsys) -> None:
    order_admission = admission()
    inspection = UsDaySessionInspection(readiness(order_admission, 3).broker_state, AT, False, True, True, ())
    dependencies = _dependencies(order_admission, inspection)

    preflight_exit = main(
        (
            "preflight",
            "--execution-database",
            str(tmp_path / "execution.sqlite3"),
            "--watch-database",
            str(tmp_path / "watch.sqlite3"),
        ),
        dependencies,
    )
    preflight_payload = json.loads(capsys.readouterr().out)
    recover_exit = main(
        ("recover", "--execution-database", str(tmp_path / "execution.sqlite3")),
        dependencies,
    )
    recover_payload = json.loads(capsys.readouterr().out)

    assert preflight_exit == 0
    assert preflight_payload["result"] == "ready"
    assert recover_exit == 0
    assert recover_payload["result"] == "recovered"


def test_finalize_writes_flat_censored_no_setup_terminal(tmp_path: Path, capsys) -> None:
    repository = _clean_repository(tmp_path)
    source_path = Path("outputs/source/paper_recommendations.sqlite3")
    _ = PaperStore(repository / source_path)
    order_admission = admission()
    inspection = UsDaySessionInspection(
        readiness(order_admission, 3).broker_state,
        AT.replace(hour=20),
        False,
        True,
        True,
        (),
    )
    terminal_path = repository / "outputs/acceptance/us_day/sessions/2026-07-14.json"

    exit_code = main(
        (
            "finalize",
            "--delivery-database",
            str(repository / "outputs/delivery.sqlite3"),
            "--execution-database",
            str(repository / "outputs/execution.sqlite3"),
            "--repository",
            str(repository),
            "--session-id",
            "XNYS-2026-07-14",
            "--source-artifact",
            str(source_path),
            "--strategy-version",
            "orb-v1",
            "--terminal-output",
            str(terminal_path),
        ),
        _dependencies(order_admission, inspection),
    )

    terminal = UsDaySessionTerminal.model_validate_json(terminal_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["result"] == "censored"
    assert terminal.reasons == ("censored_no_setup",)


def test_finalize_rejects_censored_terminal_when_orb_recommendation_exists(tmp_path: Path, capsys) -> None:
    # Given: the immutable source database contains a real-session ORB recommendation.
    repository = _clean_repository(tmp_path)
    source_path = Path("outputs/source/paper_recommendations.sqlite3")
    store = PaperStore(repository / source_path)
    store.save(
        Recommendation(
            "orb-setup",
            "FAST",
            "opening_range_breakout",
            AT,
            10.5,
            10.0,
            11.0,
            11.5,
            RecommendationState.TIME_EXIT,
            "causal setup",
        )
    )
    order_admission = admission()
    inspection = UsDaySessionInspection(
        readiness(order_admission, 3).broker_state,
        AT.replace(hour=20),
        False,
        True,
        True,
        (),
    )
    terminal_path = repository / "outputs/acceptance/us_day/sessions/false-censored.json"

    # When: the operator attempts to attest the session as having no setup.
    exit_code = main(
        (
            "finalize",
            "--delivery-database",
            str(repository / "outputs/delivery.sqlite3"),
            "--execution-database",
            str(repository / "outputs/execution.sqlite3"),
            "--repository",
            str(repository),
            "--session-id",
            "XNYS-2026-07-14",
            "--source-artifact",
            str(source_path),
            "--strategy-version",
            "orb-v1",
            "--terminal-output",
            str(terminal_path),
        ),
        _dependencies(order_admission, inspection),
    )

    # Then: false censored evidence is blocked before a terminal is written.
    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["result"] == "blocked"
    assert not terminal_path.exists()


def test_finalize_rejects_missing_source_before_projecting_outcome(tmp_path: Path, capsys) -> None:
    repository = _clean_repository(tmp_path)
    order_admission = admission()
    inspection = UsDaySessionInspection(
        readiness(order_admission, 3).broker_state,
        AT.replace(hour=20),
        False,
        True,
        True,
        (),
    )
    delivery_database = repository / "outputs/delivery.sqlite3"

    exit_code = main(
        (
            "finalize",
            "--delivery-database",
            str(delivery_database),
            "--execution-database",
            str(repository / "outputs/execution.sqlite3"),
            "--repository",
            str(repository),
            "--session-id",
            "XNYS-2026-07-14",
            "--source-artifact",
            "outputs/source/missing.json",
            "--strategy-version",
            "orb-v1",
            "--terminal-output",
            str(repository / "outputs/acceptance/us_day/sessions/missing.json"),
        ),
        _dependencies(order_admission, inspection),
    )

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["result"] == "blocked"
    assert not delivery_database.exists()


def test_evidence_command_writes_real_three_session_bundle(tmp_path: Path, capsys) -> None:
    repository = _clean_repository(tmp_path)
    commit_sha = _git(repository, "rev-parse", "HEAD")
    terminal_paths: list[Path] = []
    for index in range(3):
        terminal = _terminal(index, natural=index == 0).model_copy(update={"commit_sha": commit_sha})
        source = repository / terminal.source_artifacts[0].path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"source-{index}", encoding="utf-8")
        relative = Path(f"outputs/acceptance/us_day/sessions/session-{index}.json")
        write_private_stable_report(repository / relative, terminal.model_dump_json() + "\n")
        terminal_paths.append(relative)
    arguments = ["evidence", "--repository", str(repository)]
    for path in terminal_paths:
        arguments.extend(("--terminal", str(path)))

    exit_code = main(arguments)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["operating_product_complete"] is True
    assert (repository / "outputs/acceptance/day/manifest.json").is_file()


def _dependencies(
    order_admission: PaperOrderAdmissionRequest,
    inspection: UsDaySessionInspection,
) -> UsDayCliDependencies:
    return UsDayCliDependencies(
        lambda: AT,
        CapturingRunnerFactory(StaticRunner(_completed_result())),
        lambda _path, _at: order_admission,
        StaticReadOnlyOperations(inspection),
    )


def _completed_result() -> UsDayOperatingResult:
    order_admission = admission()
    return UsDayOperatingResult(
        UsDayOperatingStatus.COMPLETED,
        (UsDayOperatingTransition.FLAT, UsDayOperatingTransition.RECONCILED),
        (),
        "XNYS-2026-07-14",
        order_admission.candidate_intent.strategy_version,
        order_admission.candidate_intent.intent_id,
        None,
        None,
        "b" * 64,
    )

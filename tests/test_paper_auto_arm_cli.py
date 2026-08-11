from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from tests.test_paper_auto_arm_policy import _authority
from trading_agent.hermes_arm_request import HermesArmAuthority, HermesArmScope
from trading_agent.paper_auto_arm_cli import (
    PaperAutoArmCliDependencies,
    PaperAutoArmPolicyCommand,
    main,
    parser,
)

NOW = dt.datetime(2026, 7, 14, 14, 0, tzinfo=dt.UTC)


def test_cli_help_exposes_provision_verify_and_status() -> None:
    # Given / When: the policy CLI help is rendered.
    help_text = parser().format_help()

    # Then: every lifecycle action is discoverable.
    assert all(action in help_text for action in ("provision", "verify", "status"))


def test_cli_provision_verify_and_status_are_redacted(tmp_path: Path, capsys) -> None:
    # Given: controlled current-session authority and secure policy directory.
    policy_dir = tmp_path / "config"
    policy_dir.mkdir(mode=0o700)
    policy_path = policy_dir / "paper-auto-arm.json"
    dependencies = PaperAutoArmCliDependencies(clock=lambda: NOW, authority_loader=_load_authority)
    common = _common_args(tmp_path, policy_path)

    # When: the operator provisions, verifies, and checks status.
    results: list[tuple[int, str]] = []
    for action in ("provision", "verify", "status"):
        exit_code = main((action, *common), dependencies)
        results.append((exit_code, capsys.readouterr().out))

    # Then: every action succeeds without printing the account binding.
    assert [code for code, _ in results] == [0, 0, 0]
    assert [json.loads(output)["result"] for _, output in results] == ["provisioned", "valid", "valid"]
    assert all(_authority().account_fingerprint not in output for _, output in results)


def test_cli_bad_path_fails_before_authority_access(capsys) -> None:
    # Given: a relative policy path and an authority loader that must remain untouched.
    accessed = False

    def authority_loader(_: PaperAutoArmPolicyCommand, __: HermesArmScope) -> HermesArmAuthority:
        nonlocal accessed
        accessed = True
        return _authority()

    dependencies = PaperAutoArmCliDependencies(clock=lambda: NOW, authority_loader=authority_loader)

    # When: invalid CLI input is parsed.
    exit_code = main(("status", "--policy", "relative.json"), dependencies)

    # Then: parsing fails closed before authority material is accessed.
    assert exit_code == 2
    assert not accessed
    assert _authority().account_fingerprint not in capsys.readouterr().err


def _load_authority(_command: PaperAutoArmPolicyCommand, scope: HermesArmScope) -> HermesArmAuthority:
    return _authority().model_copy(update={"scope": scope})


def _common_args(tmp_path: Path, policy_path: Path) -> tuple[str, ...]:
    return (
        "--policy",
        str(policy_path),
        "--repository",
        str(tmp_path),
        "--lane-registry",
        str(tmp_path / "lane.sqlite3"),
        "--experiment-ledger",
        str(tmp_path / "experiment.sqlite3"),
        "--session-id",
        "XNYS-2026-07-14",
    )

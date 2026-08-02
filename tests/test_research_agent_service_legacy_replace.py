from __future__ import annotations

import json
import os
import plistlib
from pathlib import Path

import pytest

from run_research_agent_runtime import main
from tests.test_research_agent_service_replace_cli import (
    ReplacementFixture,
    _argv,
    _config,
    _current_main_repository,
    _provision,
)
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.research_agent_service_config import RESEARCH_AGENT_SERVICE_LABEL


def test_replace_accepts_exact_legacy_current_and_v2_candidate(tmp_path: Path) -> None:
    fixture = _legacy_replacement_fixture(tmp_path)
    calls: list[tuple[str, ...]] = []

    code = main(_argv(fixture), runner=lambda command: calls.append(command) or 0)

    domain = f"gui/{os.getuid()}"
    assert code == 0
    assert calls == [
        ("/bin/launchctl", "bootout", domain, str(fixture.current_plist)),
        ("/bin/launchctl", "bootstrap", domain, str(fixture.candidate_plist)),
        ("/bin/launchctl", "kickstart", f"{domain}/{RESEARCH_AGENT_SERVICE_LABEL}"),
    ]


@pytest.mark.parametrize(
    "corruption",
    ["malformed", "noncanonical", "wrong_label", "relative_path", "plist_binding"],
)
def test_replace_rejects_corrupt_legacy_current_before_launchctl(
    tmp_path: Path,
    corruption: str,
) -> None:
    fixture = _legacy_replacement_fixture(tmp_path)
    if corruption == "plist_binding":
        plist_payload = fixture.current_plist.read_text(encoding="utf-8").replace(
            str(fixture.current_config),
            str((tmp_path / "private" / "wrong-current.json").absolute()),
        )
        _replace_private_text(fixture.current_plist, plist_payload)
    else:
        _corrupt_legacy_config(fixture.current_config, corruption)
    calls: list[tuple[str, ...]] = []

    code = main(_argv(fixture), runner=lambda command: calls.append(command) or 0)

    assert code == 2
    assert calls == []


def test_replace_rejects_current_project_different_from_candidate_main(tmp_path: Path) -> None:
    current_scope = tmp_path / "current-scope"
    candidate_scope = tmp_path / "candidate-scope"
    current_scope.mkdir()
    candidate_scope.mkdir()
    current_repository = _current_main_repository(current_scope)
    candidate_repository = _current_main_repository(candidate_scope)
    current_config, current_plist = _legacy_pair(current_scope, current_repository, "legacy-current")
    candidate_config, candidate_plist = _provision(candidate_scope, candidate_repository, "candidate")
    fixture = ReplacementFixture(
        repository=candidate_repository,
        current_config=current_config,
        current_plist=current_plist,
        candidate_config=candidate_config,
        candidate_plist=candidate_plist,
    )
    calls: list[tuple[str, ...]] = []

    code = main(_argv(fixture), runner=lambda command: calls.append(command) or 0)

    assert code == 2
    assert calls == []


def _corrupt_legacy_config(path: Path, corruption: str) -> None:
    payload = path.read_text(encoding="utf-8")
    if corruption == "malformed":
        replacement = "{}\n"
    elif corruption == "noncanonical":
        replacement = f"{payload}\n"
    else:
        decoded = json.loads(payload)
        decoded["label" if corruption == "wrong_label" else "project_root"] = (
            "wrong.research-agent" if corruption == "wrong_label" else "relative/project"
        )
        replacement = json.dumps(decoded, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    _replace_private_text(path, replacement)


def test_replace_never_accepts_legacy_candidate(tmp_path: Path) -> None:
    repository = _current_main_repository(tmp_path)
    current_config, current_plist = _provision(tmp_path, repository, "current")
    candidate_config, candidate_plist = _legacy_pair(tmp_path, repository, "legacy-candidate")
    fixture = ReplacementFixture(
        repository=repository,
        current_config=current_config,
        current_plist=current_plist,
        candidate_config=candidate_config,
        candidate_plist=candidate_plist,
    )
    calls: list[tuple[str, ...]] = []

    code = main(_argv(fixture), runner=lambda command: calls.append(command) or 0)

    assert code == 2
    assert calls == []


def test_verify_remains_strict_v2_for_legacy_pair(tmp_path: Path) -> None:
    repository = _current_main_repository(tmp_path)
    config_path, plist_path = _legacy_pair(tmp_path, repository, "legacy-current")

    code = main(("verify", "--config", str(config_path), "--plist", str(plist_path)))

    assert code == 2


def _legacy_replacement_fixture(tmp_path: Path) -> ReplacementFixture:
    repository = _current_main_repository(tmp_path)
    current_config, current_plist = _legacy_pair(tmp_path, repository, "legacy-current")
    candidate_config, candidate_plist = _provision(tmp_path, repository, "candidate")
    return ReplacementFixture(
        repository=repository,
        current_config=current_config,
        current_plist=current_plist,
        candidate_config=candidate_config,
        candidate_plist=candidate_plist,
    )


def _legacy_pair(tmp_path: Path, repository: Path, name: str) -> tuple[Path, Path]:
    config = _config(tmp_path, repository)
    payload = config.model_dump(mode="json")
    payload["schema_version"] = 1
    systematic = payload["systematic"]
    systematic["input_csv"] = str((tmp_path / "legacy" / "input.csv").absolute())
    systematic["data_foundation_manifest"] = str((tmp_path / "legacy" / "foundation.json").absolute())
    del systematic["input_activation"]
    config_path = (tmp_path / "private" / f"{name}.json").absolute()
    plist_path = (tmp_path / "private" / f"{name}.plist").absolute()
    config_text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    plist_text = plistlib.dumps(
        {
            "KeepAlive": True,
            "Label": RESEARCH_AGENT_SERVICE_LABEL,
            "ProcessType": "Background",
            "ProgramArguments": [
                str(config.uv_path),
                "run",
                "--offline",
                "python",
                str(repository / "run_research_agent_runtime.py"),
                "run",
                "--config",
                str(config_path),
            ],
            "RunAtLoad": True,
            "StandardErrorPath": "/dev/null",
            "StandardOutPath": "/dev/null",
            "ThrottleInterval": 30,
            "Umask": 0o077,
            "WorkingDirectory": str(repository),
        },
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    ).decode("utf-8")
    assert publish_private_immutable_text(config_path, config_text)
    assert publish_private_immutable_text(plist_path, plist_text)
    return config_path, plist_path


def _replace_private_text(path: Path, payload: str) -> None:
    path.unlink()
    path.write_text(payload, encoding="utf-8")
    path.chmod(0o600)

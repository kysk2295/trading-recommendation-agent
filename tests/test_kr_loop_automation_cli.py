from __future__ import annotations

import json
from pathlib import Path

import run_kr_loop_automation as cli


def test_cli_help_and_bad_config_fail_closed(tmp_path: Path, capsys) -> None:
    assert cli.main(("--help",)) == 0
    assert cli.main(("status", "--config", str(tmp_path / "missing.json"))) == 2
    captured = capsys.readouterr()
    assert "KR Loop automation" in captured.out
    assert "invalid KR Loop automation request" in captured.err


def test_status_prints_redacted_control_plane_summary(tmp_path: Path, capsys) -> None:
    from tests.test_kr_loop_automation import _automation_config

    _config, path = _automation_config(tmp_path)
    assert cli.main(("status", "--config", str(path))) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "active_release": None,
        "candidate_count": 0,
        "paper_only": True,
        "release": None,
        "trading_authority": False,
    }

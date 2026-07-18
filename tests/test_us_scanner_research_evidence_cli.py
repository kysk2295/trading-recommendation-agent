from __future__ import annotations

import stat
from pathlib import Path

import pytest

import run_us_scanner_research_evidence as cli
from tests.test_us_scanner_research_evidence import _scanner_store
from trading_agent.research_evidence_artifact import load_research_evidence_artifact
from trading_agent.research_evidence_models import ClaimCorroborationStatus


def test_help_is_available() -> None:
    with pytest.raises(SystemExit) as raised:
        _ = cli.parse_args(["--help"])

    assert raised.value.code == 0


def test_cli_writes_private_unconfirmed_artifact_and_exact_replay(
    tmp_path: Path,
) -> None:
    store = _scanner_store(tmp_path)
    arguments = _arguments(tmp_path, store.path)

    first = cli.main(arguments)
    second = cli.main(arguments)

    assert first == second == 0
    report = (tmp_path / "report" / cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "result: ready" in report
    assert "evidence artifact: replay" in report
    assert "unconfirmed claim: 1" in report
    artifacts = tuple((tmp_path / "artifacts").glob("research_evidence_*.json"))
    assert len(artifacts) == 1
    model = load_research_evidence_artifact(artifacts[0])
    assert model.claims[0].corroboration_status is ClaimCorroborationStatus.UNCONFIRMED
    content = artifacts[0].read_bytes()
    assert b"raw_receipt_ref" not in content
    assert b"kis/ranking" not in content
    assert stat.S_IMODE((tmp_path / "artifacts").stat().st_mode) == 0o700
    assert stat.S_IMODE(artifacts[0].stat().st_mode) == 0o600


def test_nonprivate_store_blocks_without_artifact(tmp_path: Path) -> None:
    store = _scanner_store(tmp_path)
    store.path.chmod(0o644)

    code = cli.main(_arguments(tmp_path, store.path))

    assert code == 1
    assert not (tmp_path / "artifacts").exists()


def _arguments(tmp_path: Path, store: Path) -> list[str]:
    return [
        "--scanner-store",
        str(store),
        "--artifact-root",
        str(tmp_path / "artifacts"),
        "--output-dir",
        str(tmp_path / "report"),
    ]

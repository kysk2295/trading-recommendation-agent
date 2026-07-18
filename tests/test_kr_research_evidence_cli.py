from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

import run_kr_research_evidence as cli
from tests.kr_research_fixtures import (
    CLASSIFICATION_RUN_ID,
    CLASSIFIED_AT,
    CYCLE_ID,
    append_kr_research_input,
)
from trading_agent.kr_theme_models import KrCatalystSource
from trading_agent.kr_theme_store import KrThemeStore
from trading_agent.research_evidence_artifact import load_research_evidence_artifact
from trading_agent.research_evidence_models import ClaimCorroborationStatus


def test_help_is_available() -> None:
    with pytest.raises(SystemExit) as raised:
        _ = cli.parse_args(["--help"])

    assert raised.value.code == 0


def test_cli_writes_corroborated_private_artifact_and_exact_replay(tmp_path: Path) -> None:
    database = _database(tmp_path)
    manifest = _manifest(tmp_path)
    arguments = _arguments(tmp_path, database, manifest)

    first = cli.main(arguments)
    second = cli.main(arguments)

    assert first == second == 0
    report = (tmp_path / "report" / cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "result: ready" in report
    assert "evidence artifact: replay" in report
    assert "corroborated claim: 1" in report
    artifacts = tuple((tmp_path / "artifacts").glob("research_evidence_*.json"))
    assert len(artifacts) == 1
    model = load_research_evidence_artifact(artifacts[0])
    assert model.claims[0].corroboration_status is ClaimCorroborationStatus.CORROBORATED
    assert b"raw_receipt_ref" not in artifacts[0].read_bytes()
    assert b"Synthetic semiconductor supply contract" not in artifacts[0].read_bytes()
    assert stat.S_IMODE((tmp_path / "artifacts").stat().st_mode) == 0o700
    assert stat.S_IMODE(artifacts[0].stat().st_mode) == 0o600


def test_nonprivate_database_blocks_without_artifact(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.chmod(0o644)

    code = cli.main(_arguments(tmp_path, database, _manifest(tmp_path)))

    assert code == 1
    assert not (tmp_path / "artifacts").exists()
    assert not (tmp_path / "report").exists()


def test_database_through_symlinked_parent_blocks_without_artifact(tmp_path: Path) -> None:
    private_root = tmp_path / "private"
    private_root.mkdir()
    database = _database(private_root)
    alias = tmp_path / "alias"
    alias.symlink_to(private_root, target_is_directory=True)

    code = cli.main(_arguments(tmp_path, alias / database.name, _manifest(tmp_path)))

    assert code == 1
    assert not (tmp_path / "artifacts").exists()


def test_same_version_tampered_rules_fail_replay(tmp_path: Path) -> None:
    database = _database(tmp_path)
    manifest = _manifest(tmp_path)
    rules_path = manifest.parent / "rules.json"
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    rules["rules"][0]["keywords"] = ["biotechnology"]
    rules_path.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")

    code = cli.main(_arguments(tmp_path, database, manifest))

    assert code == 1
    assert not (tmp_path / "artifacts").exists()


def _database(tmp_path: Path) -> Path:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    append_kr_research_input(store, KrCatalystSource.DART)
    append_kr_research_input(store, KrCatalystSource.NEWS)
    return store.path


def _manifest(tmp_path: Path) -> Path:
    root = tmp_path / "manifest"
    root.mkdir(exist_ok=True)
    rules = {
        "schema_version": 1,
        "classifier_version": "kr-keyword-v1",
        "prompt_version": "no-prompt-v1",
        "rules": [
            {
                "schema_version": 1,
                "theme_name": "반도체",
                "keywords": ["semiconductor"],
                "related_symbols": [
                    {
                        "schema_version": 1,
                        "symbol": "005930",
                        "relation": "direct_business",
                        "rationale": "registered deterministic rule",
                    }
                ],
            }
        ],
    }
    (root / "rules.json").write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "collection_cycle_id": CYCLE_ID,
        "rules_path": "rules.json",
        "classification_run_id": CLASSIFICATION_RUN_ID,
        "classified_at": CLASSIFIED_AT.isoformat(),
        "projected_at": (CLASSIFIED_AT.replace(second=CLASSIFIED_AT.second + 1)).isoformat(),
        "validity_seconds": 300,
        "producer_strategy_version": "kr-theme-v1",
    }
    path = root / "projection-run.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


def _arguments(tmp_path: Path, database: Path, manifest: Path) -> list[str]:
    return [
        "--database",
        str(database),
        "--run-manifest",
        str(manifest),
        "--artifact-root",
        str(tmp_path / "artifacts"),
        "--output-dir",
        str(tmp_path / "report"),
    ]

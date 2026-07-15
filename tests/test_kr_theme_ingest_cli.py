from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
import typer

import run_kr_theme_ingest
from trading_agent.kr_theme_store import KrThemeStore


def test_cli_ingests_synthetic_manifest_and_restart_is_idempotent(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "input")
    database = tmp_path / "ledger" / "kr-theme.sqlite3"
    output = tmp_path / "report"

    run_kr_theme_ingest.main(str(manifest), str(database), str(output))
    first_report = (output / "kr_theme_ingest_summary_ko.md").read_text(encoding="utf-8")
    run_kr_theme_ingest.main(str(manifest), str(database), str(output))
    second_report = (output / "kr_theme_ingest_summary_ko.md").read_text(encoding="utf-8")

    store = KrThemeStore(database)
    assert len(store.catalysts()) == 2
    assert len(store.observations()) == 2
    assert len(store.cycles()) == 1
    assert "신규 원문: 2" in first_report
    assert "신규 관측: 2" in first_report
    assert "완전 cycle: 예" in first_report
    assert "신규 원문: 0" in second_report
    assert "신규 관측: 0" in second_report
    assert "synthetic semiconductor catalyst" not in first_report
    assert "005930" not in first_report


def test_cli_preserves_explicit_source_failure_as_incomplete(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "input"
    manifest_dir.mkdir()
    document = _manifest_document(items=False)
    cycle = cast(dict[str, object], document["cycle"])
    coverage = cast(list[dict[str, object]], cycle["coverage"])
    coverage[2] = {
        "schema_version": 1,
        "source": "news",
        "status": "failed",
        "record_count": 0,
        "failure_code": "http_503",
    }
    manifest = manifest_dir / "manifest.json"
    manifest.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    database = tmp_path / "kr-theme.sqlite3"
    output = tmp_path / "report"

    run_kr_theme_ingest.main(str(manifest), str(database), str(output))

    report = (output / "kr_theme_ingest_summary_ko.md").read_text(encoding="utf-8")
    assert KrThemeStore(database).cycles()[0].complete is False
    assert "완전 cycle: 아니오" in report
    assert "news · failed · 0 · http_503" in report


def test_cli_invalid_manifest_fails_before_database_creation(tmp_path: Path) -> None:
    database = tmp_path / "kr-theme.sqlite3"

    with pytest.raises(typer.BadParameter):
        run_kr_theme_ingest.main(
            str(tmp_path / "missing.json"),
            str(database),
            str(tmp_path / "report"),
        )

    assert not database.exists()


def _write_manifest(directory: Path) -> Path:
    directory.mkdir()
    (directory / "kis.json").write_text(
        '{"rank":1,"symbol":"005930"}',
        encoding="utf-8",
    )
    (directory / "news.json").write_text(
        '{"title":"synthetic semiconductor catalyst"}',
        encoding="utf-8",
    )
    path = directory / "manifest.json"
    path.write_text(
        json.dumps(_manifest_document(items=True), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _manifest_document(*, items: bool) -> dict[str, object]:
    catalysts: list[dict[str, object]] = []
    counts = {"kis_ranking": 0, "news": 0}
    if items:
        counts = {"kis_ranking": 1, "news": 1}
        catalysts = [
            {
                "schema_version": 1,
                "source": "kis_ranking",
                "source_record_id": "kis-ranking://synthetic/001",
                "publisher_id": "kis_synthetic",
                "published_at": None,
                "observed_at": "2026-07-15T09:01:00+09:00",
                "content_type": "application/json",
                "payload_path": "kis.json",
            },
            {
                "schema_version": 1,
                "source": "news",
                "source_record_id": "news://synthetic/001",
                "publisher_id": "synthetic_news",
                "published_at": "2026-07-15T09:00:00+09:00",
                "observed_at": "2026-07-15T09:00:30+09:00",
                "content_type": "application/json",
                "payload_path": "news.json",
            },
        ]
    return {
        "schema_version": 1,
        "cycle": {
            "schema_version": 1,
            "collection_cycle_id": "kr-theme-fixture-001",
            "started_at": "2026-07-15T09:00:00+09:00",
            "completed_at": "2026-07-15T09:02:00+09:00",
            "coverage": [
                _coverage("dart", 0),
                _coverage("kis_ranking", counts["kis_ranking"]),
                _coverage("news", counts["news"]),
                _coverage("volume_surge", 0),
            ],
        },
        "catalysts": catalysts,
    }


def _coverage(source: str, count: int) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source": source,
        "status": "success",
        "record_count": count,
        "failure_code": None,
    }

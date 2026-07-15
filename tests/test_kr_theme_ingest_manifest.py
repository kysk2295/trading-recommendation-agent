from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from trading_agent.kr_theme_ingest_manifest import (
    KrThemeManifestError,
    load_kr_theme_ingest_manifest,
)
from trading_agent.kr_theme_models import KrCatalystSource


def test_manifest_loads_payloads_and_builds_causal_records(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)

    loaded = load_kr_theme_ingest_manifest(manifest_path)

    assert loaded.cycle.complete is True
    assert tuple(item.record.source for item in loaded.catalysts) == (
        KrCatalystSource.KIS_RANKING,
        KrCatalystSource.NEWS,
    )
    assert loaded.catalysts[0].raw_payload == b'{"rank":1,"symbol":"005930"}'
    assert loaded.catalysts[0].observation.collection_cycle_id == loaded.cycle.collection_cycle_id
    assert "raw_payload" not in repr(loaded.catalysts[0])


@pytest.mark.parametrize("fault", ["traversal", "duplicate", "count", "empty"])
def test_manifest_rejects_unsafe_or_inconsistent_items(
    tmp_path: Path,
    fault: str,
) -> None:
    document = _manifest_document()
    payload_path = tmp_path / "news.json"
    payload_path.write_text('{"title":"synthetic"}', encoding="utf-8")
    items = cast(list[dict[str, object]], document["catalysts"])
    news_item = items[1]
    if fault == "traversal":
        news_item["payload_path"] = "../outside.json"
    elif fault == "duplicate":
        items.append(dict(news_item))
        cycle = cast(dict[str, object], document["cycle"])
        coverage = cast(list[dict[str, object]], cycle["coverage"])
        coverage[2]["record_count"] = 2
    elif fault == "count":
        cycle = cast(dict[str, object], document["cycle"])
        coverage = cast(list[dict[str, object]], cycle["coverage"])
        coverage[2]["record_count"] = 2
    else:
        payload_path.write_bytes(b"")
    (tmp_path / "kis.json").write_text('{"rank":1,"symbol":"005930"}', encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(KrThemeManifestError):
        load_kr_theme_ingest_manifest(manifest_path)


def test_manifest_rejects_symlink_escape(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "manifest"
    manifest_dir.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"title":"outside"}', encoding="utf-8")
    (manifest_dir / "news.json").symlink_to(outside)
    (manifest_dir / "kis.json").write_text(
        '{"rank":1,"symbol":"005930"}',
        encoding="utf-8",
    )
    document = _manifest_document()
    manifest_path = manifest_dir / "manifest.json"
    manifest_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(KrThemeManifestError):
        load_kr_theme_ingest_manifest(manifest_path)


def _write_manifest(directory: Path) -> Path:
    (directory / "kis.json").write_text(
        '{"rank":1,"symbol":"005930"}',
        encoding="utf-8",
    )
    (directory / "news.json").write_text(
        '{"title":"synthetic"}',
        encoding="utf-8",
    )
    path = directory / "manifest.json"
    path.write_text(json.dumps(_manifest_document(), ensure_ascii=False), encoding="utf-8")
    return path


def _manifest_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "cycle": {
            "schema_version": 1,
            "collection_cycle_id": "kr-theme-fixture-001",
            "started_at": "2026-07-15T09:00:00+09:00",
            "completed_at": "2026-07-15T09:02:00+09:00",
            "coverage": [
                _coverage("dart", 0),
                _coverage("kis_ranking", 1),
                _coverage("news", 1),
                _coverage("volume_surge", 0),
            ],
        },
        "catalysts": [
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
        ],
    }


def _coverage(source: str, count: int) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source": source,
        "status": "success",
        "record_count": count,
        "failure_code": None,
    }

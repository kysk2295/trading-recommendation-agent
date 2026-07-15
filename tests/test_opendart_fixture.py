from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import cast

import pytest

from trading_agent.opendart_fixture import (
    OpenDartFixtureError,
    load_opendart_fixture,
)

COLLECTION_DATE = dt.date(2026, 7, 15)


def test_fixture_loads_all_raw_pages_with_fixed_receipt_times(tmp_path: Path) -> None:
    manifest = _write_fixture(tmp_path)

    fetcher = load_opendart_fixture(manifest, collection_date=COLLECTION_DATE)
    first = fetcher.fetch_page(COLLECTION_DATE, page_no=1)

    assert first.request_key == "opendart:list:20260715:page:1"
    assert first.received_at == dt.datetime(2026, 7, 15, 9, 1, tzinfo=dt.UTC)
    assert json.loads(first.raw_payload)["status"] == "000"
    assert "Synthetic private report" not in repr(fetcher)
    assert "raw_payload" not in repr(first)


@pytest.mark.parametrize(
    "fault",
    ("absolute", "traversal", "duplicate_page", "missing_page", "empty"),
)
def test_fixture_rejects_unsafe_or_incomplete_pages(
    tmp_path: Path,
    fault: str,
) -> None:
    document = _manifest_document()
    pages = cast(list[dict[str, object]], document["pages"])
    if fault == "absolute":
        pages[0]["payload_path"] = str(tmp_path / "page-1.json")
    elif fault == "traversal":
        pages[0]["payload_path"] = "../outside.json"
    elif fault == "duplicate_page":
        pages.append(dict(pages[0]))
    elif fault == "missing_page":
        pages[0]["page_no"] = 2
    else:
        (tmp_path / "page-1.json").write_bytes(b"")
    if not (tmp_path / "page-1.json").exists():
        (tmp_path / "page-1.json").write_text("{}", encoding="utf-8")
    manifest = tmp_path / "fixture-manifest.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(OpenDartFixtureError):
        _ = load_opendart_fixture(manifest, collection_date=COLLECTION_DATE)


def test_fixture_rejects_symlink_payload_escape(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (fixture_dir / "page-1.json").symlink_to(outside)
    manifest = fixture_dir / "fixture-manifest.json"
    manifest.write_text(json.dumps(_manifest_document()), encoding="utf-8")

    with pytest.raises(OpenDartFixtureError):
        _ = load_opendart_fixture(manifest, collection_date=COLLECTION_DATE)


def test_fixture_rejects_wrong_collection_date_at_fetch(tmp_path: Path) -> None:
    fetcher = load_opendart_fixture(
        _write_fixture(tmp_path),
        collection_date=COLLECTION_DATE,
    )

    with pytest.raises(OpenDartFixtureError):
        _ = fetcher.fetch_page(dt.date(2026, 7, 16), page_no=1)


def _write_fixture(directory: Path) -> Path:
    (directory / "page-1.json").write_text(
        json.dumps(
            {
                "status": "000",
                "message": "normal",
                "page_no": 1,
                "page_count": 100,
                "total_count": 0,
                "total_page": 0,
                "list": [],
                "private_test_marker": "Synthetic private report",
            }
        ),
        encoding="utf-8",
    )
    manifest = directory / "fixture-manifest.json"
    manifest.write_text(json.dumps(_manifest_document()), encoding="utf-8")
    return manifest


def _manifest_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "pages": [
            {
                "schema_version": 1,
                "page_no": 1,
                "received_at": "2026-07-15T09:01:00+00:00",
                "http_status": 200,
                "content_type": "application/json",
                "payload_path": "page-1.json",
            }
        ],
    }

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import cast

import pytest

from trading_agent.kis_kr_ranking import KisKrRankingKind
from trading_agent.kis_kr_ranking_fixture import (
    KisKrRankingFixtureError,
    load_kis_kr_ranking_fixture,
)

COLLECTION_DATE = dt.date(2026, 7, 16)
RECEIVED_AT = "2026-07-16T10:01:00+09:00"


def test_fixture_loads_happy_path_pages_in_kind_order(tmp_path: Path) -> None:
    manifest = _write_happy_manifest(tmp_path)
    fetcher = load_kis_kr_ranking_fixture(
        manifest,
        collection_date=COLLECTION_DATE,
    )
    first = fetcher.fetch_page(
        KisKrRankingKind.FLUCTUATION,
        page_no=1,
        attempt=1,
        tr_cont="",
    )
    second = fetcher.fetch_page(
        KisKrRankingKind.VOLUME,
        page_no=1,
        attempt=1,
        tr_cont="",
    )

    assert first.kind is KisKrRankingKind.FLUCTUATION
    assert first.page_no == 1
    assert first.attempt == 1
    assert first.request_tr_cont == ""
    assert first.response_tr_cont == ""
    assert first.request_key == "kis-kr:fluctuation:p1:a1:rq-:rs-"
    assert first.received_at == dt.datetime(
        2026, 7, 16, 10, 1, tzinfo=dt.timezone(dt.timedelta(hours=9))
    )
    assert first.status_code == 200
    assert first.content_type == "application/json"
    assert json.loads(first.raw_payload)["rt_cd"] == "0"
    assert second.kind is KisKrRankingKind.VOLUME
    assert second.request_key == "kis-kr:volume:p1:a1:rq-:rs-"
    assert "Synthetic" not in repr(fetcher)
    assert "raw_payload" not in repr(first)


def test_fixture_rejects_payload_path_escape(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, payload_path="../outside.json")
    with pytest.raises(KisKrRankingFixtureError):
        _ = load_kis_kr_ranking_fixture(manifest, collection_date=COLLECTION_DATE)


def test_fixture_rejects_absolute_payload_path(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    document = _manifest_document()
    pages = cast(list[dict[str, object]], document["pages"])
    pages[0]["payload_path"] = str(outside)
    pages[0]["kind"] = "fluctuation"
    pages[0]["page_no"] = 1
    pages[0]["attempt"] = 1
    pages[0]["request_tr_cont"] = ""
    pages[0]["response_tr_cont"] = ""
    (tmp_path / "fluctuation-page-1.json").write_text("{}", encoding="utf-8")
    manifest = tmp_path / "fixture-manifest.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(KisKrRankingFixtureError):
        _ = load_kis_kr_ranking_fixture(manifest, collection_date=COLLECTION_DATE)


def test_fixture_rejects_symlink_payload_escape(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (fixture_dir / "fluctuation-page-1.json").symlink_to(outside)
    (fixture_dir / "volume-page-1.json").write_text("{}", encoding="utf-8")
    document = _happy_document()
    pages = cast(list[dict[str, object]], document["pages"])
    pages[0]["payload_path"] = "fluctuation-page-1.json"
    pages[1]["payload_path"] = "volume-page-1.json"
    manifest = fixture_dir / "fixture-manifest.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(KisKrRankingFixtureError):
        _ = load_kis_kr_ranking_fixture(manifest, collection_date=COLLECTION_DATE)


def test_fixture_rejects_empty_payload(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    (fixture_dir / "fluctuation-page-1.json").write_bytes(b"")
    (fixture_dir / "volume-page-1.json").write_text("{}", encoding="utf-8")
    manifest = fixture_dir / "fixture-manifest.json"
    manifest.write_text(json.dumps(_happy_document()), encoding="utf-8")
    with pytest.raises(KisKrRankingFixtureError):
        _ = load_kis_kr_ranking_fixture(manifest, collection_date=COLLECTION_DATE)


def test_fixture_rejects_duplicate_request_identity(tmp_path: Path) -> None:
    document = _happy_document()
    pages = cast(list[dict[str, object]], document["pages"])
    pages.append(dict(pages[0]))
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    (fixture_dir / "fluctuation-page-1.json").write_text("{}", encoding="utf-8")
    (fixture_dir / "volume-page-1.json").write_text("{}", encoding="utf-8")
    manifest = fixture_dir / "fixture-manifest.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(KisKrRankingFixtureError):
        _ = load_kis_kr_ranking_fixture(manifest, collection_date=COLLECTION_DATE)


def test_fixture_rejects_page_gap_and_missing_kind(tmp_path: Path) -> None:
    document = _happy_document()
    pages = cast(list[dict[str, object]], document["pages"])
    pages[0]["page_no"] = 2
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    (fixture_dir / "fluctuation-page-1.json").write_text("{}", encoding="utf-8")
    (fixture_dir / "volume-page-1.json").write_text("{}", encoding="utf-8")
    manifest = fixture_dir / "fixture-manifest.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(KisKrRankingFixtureError):
        _ = load_kis_kr_ranking_fixture(manifest, collection_date=COLLECTION_DATE)

    pages[0]["page_no"] = 1
    pages[0]["kind"] = "unknown"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(KisKrRankingFixtureError):
        _ = load_kis_kr_ranking_fixture(manifest, collection_date=COLLECTION_DATE)


def test_fixture_rejects_attempt_outside_range_and_bad_continuation(
    tmp_path: Path,
) -> None:
    document = _happy_document()
    pages = cast(list[dict[str, object]], document["pages"])
    pages[0]["attempt"] = 3
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    (fixture_dir / "fluctuation-page-1.json").write_text("{}", encoding="utf-8")
    (fixture_dir / "volume-page-1.json").write_text("{}", encoding="utf-8")
    manifest = fixture_dir / "fixture-manifest.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(KisKrRankingFixtureError):
        _ = load_kis_kr_ranking_fixture(manifest, collection_date=COLLECTION_DATE)

    pages[0]["attempt"] = 1
    pages[0]["request_tr_cont"] = "M"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(KisKrRankingFixtureError):
        _ = load_kis_kr_ranking_fixture(manifest, collection_date=COLLECTION_DATE)

    pages[0]["request_tr_cont"] = ""
    pages[0]["response_tr_cont"] = "X"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(KisKrRankingFixtureError):
        _ = load_kis_kr_ranking_fixture(manifest, collection_date=COLLECTION_DATE)


def test_fixture_rejects_collection_date_mismatch_and_out_of_order_calls(
    tmp_path: Path,
) -> None:
    manifest = _write_happy_manifest(tmp_path)
    with pytest.raises(KisKrRankingFixtureError):
        _ = load_kis_kr_ranking_fixture(
            manifest,
            collection_date=dt.date(2026, 7, 15),
        )

    fetcher = load_kis_kr_ranking_fixture(
        manifest,
        collection_date=COLLECTION_DATE,
    )
    with pytest.raises(KisKrRankingFixtureError):
        _ = fetcher.fetch_page(
            KisKrRankingKind.VOLUME,
            page_no=1,
            attempt=1,
            tr_cont="",
        )
    _ = fetcher.fetch_page(
        KisKrRankingKind.FLUCTUATION,
        page_no=1,
        attempt=1,
        tr_cont="",
    )
    _ = fetcher.fetch_page(
        KisKrRankingKind.VOLUME,
        page_no=1,
        attempt=1,
        tr_cont="",
    )
    with pytest.raises(KisKrRankingFixtureError):
        _ = fetcher.fetch_page(
            KisKrRankingKind.VOLUME,
            page_no=1,
            attempt=1,
            tr_cont="",
        )


def test_fixture_rejects_non_regular_manifest(tmp_path: Path) -> None:
    directory = tmp_path / "not-a-file"
    directory.mkdir()
    with pytest.raises(KisKrRankingFixtureError):
        _ = load_kis_kr_ranking_fixture(directory, collection_date=COLLECTION_DATE)


def test_committed_fixture_loads() -> None:
    root = Path(__file__).resolve().parent / "fixtures" / "kis_kr_ranking"
    manifest = root / "fixture-manifest.json"
    fetcher = load_kis_kr_ranking_fixture(
        manifest,
        collection_date=COLLECTION_DATE,
    )
    first = fetcher.fetch_page(
        KisKrRankingKind.FLUCTUATION,
        page_no=1,
        attempt=1,
        tr_cont="",
    )
    second = fetcher.fetch_page(
        KisKrRankingKind.VOLUME,
        page_no=1,
        attempt=1,
        tr_cont="",
    )
    assert first.kind is KisKrRankingKind.FLUCTUATION
    assert second.kind is KisKrRankingKind.VOLUME
    assert first.received_at.astimezone(
        dt.timezone(dt.timedelta(hours=9))
    ).date() == COLLECTION_DATE
    assert second.received_at.astimezone(
        dt.timezone(dt.timedelta(hours=9))
    ).date() == COLLECTION_DATE


def _write_happy_manifest(directory: Path) -> Path:
    fixture_dir = directory / "fixture"
    fixture_dir.mkdir()
    (fixture_dir / "fluctuation-page-1.json").write_text(
        json.dumps(
            {
                "rt_cd": "0",
                "msg_cd": "0",
                "msg1": "ok",
                "output": [],
                "private_test_marker": "Synthetic private ranking",
            }
        ),
        encoding="utf-8",
    )
    (fixture_dir / "volume-page-1.json").write_text(
        json.dumps(
            {
                "rt_cd": "0",
                "msg_cd": "0",
                "msg1": "ok",
                "output": [],
            }
        ),
        encoding="utf-8",
    )
    manifest = fixture_dir / "fixture-manifest.json"
    manifest.write_text(json.dumps(_happy_document()), encoding="utf-8")
    return manifest


def _write_manifest(directory: Path, *, payload_path: str) -> Path:
    fixture_dir = directory / "fixture"
    fixture_dir.mkdir()
    (fixture_dir / "fluctuation-page-1.json").write_text("{}", encoding="utf-8")
    document = _manifest_document()
    pages = cast(list[dict[str, object]], document["pages"])
    pages[0]["payload_path"] = payload_path
    pages[0]["kind"] = "fluctuation"
    pages[0]["page_no"] = 1
    pages[0]["attempt"] = 1
    pages[0]["request_tr_cont"] = ""
    pages[0]["response_tr_cont"] = ""
    pages[0]["received_at"] = RECEIVED_AT
    pages[0]["http_status"] = 200
    pages[0]["content_type"] = "application/json"
    manifest = fixture_dir / "fixture-manifest.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    return manifest


def _happy_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "collection_date": "2026-07-16",
        "pages": [
            {
                "schema_version": 1,
                "kind": "fluctuation",
                "page_no": 1,
                "attempt": 1,
                "request_tr_cont": "",
                "response_tr_cont": "",
                "received_at": RECEIVED_AT,
                "http_status": 200,
                "content_type": "application/json",
                "payload_path": "fluctuation-page-1.json",
            },
            {
                "schema_version": 1,
                "kind": "volume",
                "page_no": 1,
                "attempt": 1,
                "request_tr_cont": "",
                "response_tr_cont": "",
                "received_at": "2026-07-16T10:02:00+09:00",
                "http_status": 200,
                "content_type": "application/json",
                "payload_path": "volume-page-1.json",
            },
        ],
    }


def _manifest_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "collection_date": "2026-07-16",
        "pages": [
            {
                "schema_version": 1,
                "kind": "fluctuation",
                "page_no": 1,
                "attempt": 1,
                "request_tr_cont": "",
                "response_tr_cont": "",
                "received_at": RECEIVED_AT,
                "http_status": 200,
                "content_type": "application/json",
                "payload_path": "fluctuation-page-1.json",
            }
        ],
    }

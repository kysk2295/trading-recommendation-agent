from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_agent.ls_nws import LsNwsWireKind
from trading_agent.ls_nws_fixture import (
    LsNwsFixtureError,
    load_ls_nws_fixture,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures/ls_nws"


def _entry(
    sequence: int,
    *,
    received_at: str = "2026-07-15T09:01:01+09:00",
    wire_kind: str = "text",
    payload_path: str = "frame-1.json",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "sequence": sequence,
        "received_at": received_at,
        "wire_kind": wire_kind,
        "payload_path": payload_path,
    }


def test_committed_fixture_replays_exact_ordered_raw_frames() -> None:
    source = load_ls_nws_fixture(FIXTURE_ROOT / "fixture-manifest.json")

    with source.open() as receiver:
        first = receiver.receive_frame(1.0)
        second = receiver.receive_frame(1.0)
        completed = receiver.receive_frame(1.0)
        completed_again = receiver.receive_frame(1.0)

    assert first is not None
    assert first.sequence == 1
    assert first.received_at.isoformat() == "2026-07-15T09:01:01+09:00"
    assert first.wire_kind is LsNwsWireKind.TEXT
    assert first.raw_payload == (FIXTURE_ROOT / "frame-000001.json").read_bytes()
    assert second is not None
    assert second.sequence == 2
    assert second.received_at.isoformat() == "2026-07-15T09:01:02+09:00"
    assert second.wire_kind is LsNwsWireKind.BINARY
    assert second.raw_payload == (FIXTURE_ROOT / "frame-000002.json").read_bytes()
    assert completed is None
    assert completed_again is None


def test_fixture_source_opens_a_fresh_receiver_each_time() -> None:
    source = load_ls_nws_fixture(FIXTURE_ROOT / "fixture-manifest.json")

    with source.open() as first_receiver:
        first = first_receiver.receive_frame(1.0)
    with source.open() as second_receiver:
        restarted = second_receiver.receive_frame(1.0)

    assert first is not None
    assert restarted == first


@pytest.mark.parametrize(
    "manifest",
    (
        {},
        {"schema_version": 1, "frames": []},
        {
            "schema_version": 1,
            "frames": [_entry(1), _entry(1, payload_path="frame-2.json")],
        },
        {
            "schema_version": 1,
            "frames": [_entry(1), _entry(3, payload_path="frame-3.json")],
        },
        {
            "schema_version": 1,
            "frames": [_entry(1), _entry(2)],
        },
        {
            "schema_version": 1,
            "frames": [_entry(1, received_at="not-a-time")],
        },
        {
            "schema_version": 1,
            "frames": [_entry(1, wire_kind="unknown")],
        },
        {
            "schema_version": 1,
            "frames": [_entry(1) | {"extra": "value"}],
        },
        {"schema_version": 1, "frames": [_entry(1)], "extra": "value"},
    ),
)
def test_fixture_rejects_invalid_manifest_shape(
    tmp_path: Path,
    manifest: dict[str, object],
) -> None:
    path = _write_fixture_files(tmp_path, manifest)

    with pytest.raises(LsNwsFixtureError):
        _ = load_ls_nws_fixture(path)


@pytest.mark.parametrize("payload_path", ("../outside.json", "/tmp/frame.json", "."))
def test_fixture_rejects_uncontained_payload_path(
    tmp_path: Path,
    payload_path: str,
) -> None:
    path = _write_fixture_files(
        tmp_path,
        {"schema_version": 1, "frames": [_entry(1, payload_path=payload_path)]},
    )

    with pytest.raises(LsNwsFixtureError):
        _ = load_ls_nws_fixture(path)


def test_fixture_rejects_symlink_manifest(tmp_path: Path) -> None:
    target = _write_fixture_files(
        tmp_path,
        {"schema_version": 1, "frames": [_entry(1)]},
    )
    link = tmp_path / "manifest-link.json"
    link.symlink_to(target)

    with pytest.raises(LsNwsFixtureError):
        _ = load_ls_nws_fixture(link)


def test_fixture_rejects_symlink_payload(tmp_path: Path) -> None:
    payload = tmp_path / "real-frame.json"
    payload.write_bytes(b"{}")
    link = tmp_path / "frame-1.json"
    link.symlink_to(payload)
    manifest = tmp_path / "fixture-manifest.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "frames": [_entry(1)]}),
        encoding="utf-8",
    )

    with pytest.raises(LsNwsFixtureError):
        _ = load_ls_nws_fixture(manifest)


def test_fixture_rejects_empty_or_oversized_payload(
    tmp_path: Path,
) -> None:
    manifest = {"schema_version": 1, "frames": [_entry(1)]}
    path = _write_fixture_files(tmp_path, manifest, payload=b"")
    with pytest.raises(LsNwsFixtureError):
        _ = load_ls_nws_fixture(path)

    (tmp_path / "frame-1.json").write_bytes(b"x" * 262_145)
    with pytest.raises(LsNwsFixtureError):
        _ = load_ls_nws_fixture(path)


def test_fixture_rejects_malformed_manifest_without_rendering_content(
    tmp_path: Path,
) -> None:
    private = "private-fixture-content"
    path = tmp_path / "fixture-manifest.json"
    path.write_text(f"{{not-json:{private}", encoding="utf-8")

    with pytest.raises(LsNwsFixtureError) as captured:
        _ = load_ls_nws_fixture(path)

    assert private not in str(captured.value)


def _write_fixture_files(
    tmp_path: Path,
    manifest: dict[str, object],
    *,
    payload: bytes = b"{}",
) -> Path:
    frames = manifest.get("frames")
    if isinstance(frames, list):
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            payload_path = frame.get("payload_path")
            if not isinstance(payload_path, str):
                continue
            candidate = Path(payload_path)
            if candidate.is_absolute() or any(
                part in {".", ".."} for part in candidate.parts
            ):
                continue
            target = tmp_path / candidate
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(payload)
    path = tmp_path / "fixture-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path

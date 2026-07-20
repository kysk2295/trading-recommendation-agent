from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
import typer

import run_sec_edgar_collect
from trading_agent.sec_edgar_fixture import load_sec_edgar_fixture
from trading_agent.sec_edgar_store import SecEdgarStore

PRIVATE_NAME = "Example Public Corp"


def test_sec_cli_fixture_happy_and_terminal_replay_are_redacted(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "fixture")
    database = tmp_path / "ledger" / "sec.sqlite3"
    output = tmp_path / "report"

    run_sec_edgar_collect.main(
        collection_id="sec-cycle-001",
        cik="0000320193",
        database=str(database),
        output_dir=str(output),
        fixture_manifest=str(manifest),
        user_agent_path=None,
    )
    first = _report(output)
    run_sec_edgar_collect.main(
        collection_id="sec-cycle-001",
        cik="0000320193",
        database=str(database),
        output_dir=str(output),
        fixture_manifest=None,
        user_agent_path=str(tmp_path / "missing.env"),
    )
    second = _report(output)

    assert len(SecEdgarStore(database).filings_for_run(_run_id())) == 2
    assert "new filing versions: 2" in first
    assert "replayed: no" in first
    assert "new filing versions: 0" in second
    assert "replayed: yes" in second
    assert PRIVATE_NAME not in first + second
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE((output / "sec_edgar_collection_summary.md").stat().st_mode) == 0o600


def test_sec_cli_rejects_fixture_and_user_agent_before_database(tmp_path: Path) -> None:
    database = tmp_path / "sec.sqlite3"

    with pytest.raises(typer.BadParameter):
        run_sec_edgar_collect.main(
            collection_id="sec-cycle-001",
            cik="0000320193",
            database=str(database),
            output_dir=str(tmp_path / "report"),
            fixture_manifest=str(tmp_path / "fixture.json"),
            user_agent_path=str(tmp_path / "sec.env"),
        )

    assert not database.exists()


def test_sec_cli_preserves_failed_raw_receipt_and_redacts_provider_body(tmp_path: Path) -> None:
    directory = tmp_path / "fixture"
    directory.mkdir()
    private_body = b"private SEC provider response"
    (directory / "error.html").write_bytes(private_body)
    manifest = directory / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "received_at": "2026-07-20T14:00:00+00:00",
                "http_status": 403,
                "content_type": "text/html",
                "payload_path": "error.html",
            }
        ),
        encoding="utf-8",
    )
    database = tmp_path / "sec.sqlite3"
    output = tmp_path / "report"

    with pytest.raises(typer.BadParameter) as captured:
        run_sec_edgar_collect.main(
            collection_id="sec-cycle-001",
            cik="0000320193",
            database=str(database),
            output_dir=str(output),
            fixture_manifest=str(manifest),
            user_agent_path=None,
        )

    stored = SecEdgarStore(database).receipt_for_collection("sec-cycle-001", "0000320193")
    assert stored is not None
    assert stored.response.raw_payload == private_body
    rendered = str(captured.value) + _report(output)
    assert private_body.decode() not in rendered
    assert "http_403" in rendered


def test_sec_cli_rejects_database_report_alias_before_collection(tmp_path: Path) -> None:
    output = tmp_path / "report"
    database = output / "sec_edgar_collection_summary.md"

    with pytest.raises(typer.BadParameter):
        run_sec_edgar_collect.main(
            collection_id="sec-cycle-001",
            cik="0000320193",
            database=str(database),
            output_dir=str(output),
            fixture_manifest=str(_manifest(tmp_path / "fixture")),
            user_agent_path=None,
        )

    assert not database.exists()


def test_sec_cli_rejects_symlinked_output_directory_before_collection(tmp_path: Path) -> None:
    real_output = tmp_path / "real-output"
    real_output.mkdir()
    output = tmp_path / "output-link"
    output.symlink_to(real_output, target_is_directory=True)
    database = tmp_path / "sec.sqlite3"

    with pytest.raises(typer.BadParameter):
        run_sec_edgar_collect.main(
            collection_id="sec-cycle-001",
            cik="0000320193",
            database=str(database),
            output_dir=str(output),
            fixture_manifest=str(_manifest(tmp_path / "fixture")),
            user_agent_path=None,
        )

    assert not database.exists()


@pytest.mark.parametrize("use_fixture", [True, False])
def test_sec_cli_recovers_orphan_before_opening_external_source(
    tmp_path: Path,
    use_fixture: bool,
) -> None:
    collection_id = "sec-cycle-orphan"
    cik = "0000320193"
    database = tmp_path / "sec.sqlite3"
    output = tmp_path / "report"
    seed = load_sec_edgar_fixture(_manifest(tmp_path / "seed"))
    response = seed.fetch_submissions(collection_id, cik)
    _ = SecEdgarStore(database).append_receipt(response)
    missing = tmp_path / "missing-source"

    run_sec_edgar_collect.main(
        collection_id=collection_id,
        cik=cik,
        database=str(database),
        output_dir=str(output),
        fixture_manifest=str(missing) if use_fixture else None,
        user_agent_path=None if use_fixture else str(missing),
    )

    run = SecEdgarStore(database).collection_run(collection_id, cik)
    assert run is not None
    assert run.status.value == "success"
    assert "new filing versions: 2" in _report(output)


def _manifest(directory: Path) -> Path:
    directory.mkdir()
    source = Path(__file__).parent / "fixtures/sec_edgar/submissions.json"
    (directory / "submissions.json").write_bytes(source.read_bytes())
    manifest = directory / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "received_at": "2026-07-20T14:00:00+00:00",
                "http_status": 200,
                "content_type": "application/json",
                "payload_path": "submissions.json",
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _report(output: Path) -> str:
    return (output / "sec_edgar_collection_summary.md").read_text(encoding="utf-8")


def _run_id() -> str:
    import hashlib

    return hashlib.sha256(b"sec-cycle-001|0000320193").hexdigest()

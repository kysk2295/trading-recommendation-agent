from __future__ import annotations

import json
import os
import re
import shutil
import stat
from pathlib import Path
from typing import cast

import pytest
import typer

import run_kr_theme_ingest
import run_kr_theme_projection
from trading_agent.contract_outbox import ContractOutboxFormatError
from trading_agent.kr_theme_store import KrThemeStore
from trading_agent.signal_contract_models import OpportunitySnapshot

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "kr_theme_projection"


def test_projection_cli_publishes_one_kr_opportunity_and_restart_is_idempotent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger" / "kr-theme.sqlite3"
    ingest_output = tmp_path / "ingest"
    projection_output = tmp_path / "projection"
    run_kr_theme_ingest.main(
        str(EXAMPLE / "ingest-manifest.json"),
        str(database),
        str(ingest_output),
    )

    run_kr_theme_projection.main(
        str(EXAMPLE / "projection-run.json"),
        str(database),
        str(projection_output),
    )
    first_report = (projection_output / "kr_theme_projection_summary_ko.md").read_text(
        encoding="utf-8"
    )
    outbox = projection_output / "opportunities.v1.jsonl"
    summary = projection_output / "kr_theme_projection_summary_ko.md"
    assert stat.S_IMODE(outbox.stat().st_mode) == 0o600
    assert stat.S_IMODE(summary.stat().st_mode) == 0o600
    outbox.chmod(0o644)
    summary.chmod(0o644)
    run_kr_theme_projection.main(
        str(EXAMPLE / "projection-run.json"),
        str(database),
        str(projection_output),
    )
    second_report = (projection_output / "kr_theme_projection_summary_ko.md").read_text(
        encoding="utf-8"
    )

    store = KrThemeStore(database)
    assert len(store.classifications()) == 1
    lines = (projection_output / "opportunities.v1.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(lines) == 1
    opportunity = OpportunitySnapshot.model_validate_json(lines[0])
    assert opportunity.strategy_lane.canonical_id == (
        "kr_equities/opportunity_manager/theme_momentum"
    )
    assert opportunity.candidates[0].symbol == "005930"
    assert opportunity.candidates[0].rank == 1
    assert "테마 수: 1" in first_report
    assert "반도체 · 대장주 005930" in first_report
    assert "신규 classification: 1" in first_report
    assert "신규 classification: 0" in second_report
    assert "합성 반도체 공급망 촉매" not in first_report
    assert "PRIVATE_SYNTHETIC_NEWS_BODY" not in first_report
    assert "news://synthetic/projection/001" not in first_report
    assert str(database) not in first_report
    assert re.search(r"\b[0-9a-f]{64}\b", first_report) is None
    assert stat.S_IMODE(outbox.stat().st_mode) == 0o600
    assert stat.S_IMODE(summary.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "database_relative",
    ("opportunities.v1.jsonl", "kr_theme_projection_summary_ko.md"),
)
def test_projection_cli_rejects_database_collision_before_classification_append(
    tmp_path: Path,
    database_relative: str,
) -> None:
    output = tmp_path / "projection"
    database = output / database_relative
    run_kr_theme_ingest.main(
        str(EXAMPLE / "ingest-manifest.json"),
        str(database),
        str(tmp_path / "ingest"),
    )

    with pytest.raises(typer.BadParameter):
        run_kr_theme_projection.main(
            str(EXAMPLE / "projection-run.json"),
            str(database),
            str(output),
        )

    store = KrThemeStore(database)
    assert store.is_initialized() is True
    assert store.classifications() == ()
    other_artifact = output / (
        "kr_theme_projection_summary_ko.md"
        if database_relative == "opportunities.v1.jsonl"
        else "opportunities.v1.jsonl"
    )
    assert not other_artifact.exists()


@pytest.mark.parametrize(
    ("artifact_relative", "link_kind"),
    (
        ("opportunities.v1.jsonl", "symlink"),
        ("kr_theme_projection_summary_ko.md", "symlink"),
        ("opportunities.v1.jsonl", "hardlink"),
        ("kr_theme_projection_summary_ko.md", "hardlink"),
    ),
)
def test_projection_cli_rejects_artifact_filesystem_alias_before_store_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_relative: str,
    link_kind: str,
) -> None:
    database = tmp_path / "kr-theme.sqlite3"
    run_kr_theme_ingest.main(
        str(EXAMPLE / "ingest-manifest.json"),
        str(database),
        str(tmp_path / "ingest"),
    )
    output = tmp_path / "projection"
    output.mkdir()
    artifact = output / artifact_relative
    if link_kind == "symlink":
        artifact.symlink_to(database)
    else:
        os.link(database, artifact)

    store_opened = False

    def unexpected_store_open(_: Path) -> object:
        nonlocal store_opened
        store_opened = True
        raise AssertionError("collision guard must run before KrThemeStore")

    monkeypatch.setattr(run_kr_theme_projection, "KrThemeStore", unexpected_store_open)

    with pytest.raises(typer.BadParameter):
        run_kr_theme_projection.main(
            str(EXAMPLE / "projection-run.json"),
            str(database),
            str(output),
        )

    assert store_opened is False
    assert KrThemeStore(database).classifications() == ()


@pytest.mark.parametrize(
    "artifact_relative",
    ("opportunities.v1.jsonl", "kr_theme_projection_summary_ko.md"),
)
@pytest.mark.parametrize(
    "sidecar_suffix",
    (".writer.lock", "-journal", "-shm", "-wal"),
)
def test_projection_cli_rejects_resolved_database_sidecar_hardlink_before_store_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_relative: str,
    sidecar_suffix: str,
) -> None:
    real_database = tmp_path / "real.sqlite3"
    run_kr_theme_ingest.main(
        str(EXAMPLE / "ingest-manifest.json"),
        str(real_database),
        str(tmp_path / "ingest"),
    )
    database_alias = tmp_path / "database-alias.sqlite3"
    database_alias.symlink_to(real_database)
    real_sidecar = Path(f"{real_database}{sidecar_suffix}")
    real_sidecar.write_text("synthetic sidecar", encoding="utf-8")
    output = tmp_path / "projection"
    output.mkdir()
    os.link(real_sidecar, output / artifact_relative)

    store_opened = False

    def unexpected_store_open(_: Path) -> object:
        nonlocal store_opened
        store_opened = True
        raise AssertionError("collision guard must run before KrThemeStore")

    monkeypatch.setattr(run_kr_theme_projection, "KrThemeStore", unexpected_store_open)

    with pytest.raises(typer.BadParameter):
        run_kr_theme_projection.main(
            str(EXAMPLE / "projection-run.json"),
            str(database_alias),
            str(output),
        )

    assert store_opened is False


def test_projection_cli_prepares_new_outbox_private_before_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "kr-theme.sqlite3"
    run_kr_theme_ingest.main(
        str(EXAMPLE / "ingest-manifest.json"),
        str(database),
        str(tmp_path / "ingest"),
    )
    output = tmp_path / "projection"
    outbox = output / "opportunities.v1.jsonl"
    observed_modes: list[int] = []

    def failing_append(path: Path, _: OpportunitySnapshot) -> bool:
        observed_modes.append(stat.S_IMODE(path.stat().st_mode))
        raise ContractOutboxFormatError(path, 1)

    monkeypatch.setattr(
        run_kr_theme_projection,
        "append_opportunity_snapshot",
        failing_append,
    )

    with pytest.raises(typer.BadParameter):
        run_kr_theme_projection.main(
            str(EXAMPLE / "projection-run.json"),
            str(database),
            str(output),
        )

    assert observed_modes == [0o600]
    assert stat.S_IMODE(outbox.stat().st_mode) == 0o600


def test_projection_cli_restores_existing_outbox_privacy_with_zero_projections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "kr-theme.sqlite3"
    run_kr_theme_ingest.main(
        str(EXAMPLE / "ingest-manifest.json"),
        str(database),
        str(tmp_path / "ingest"),
    )
    output = tmp_path / "projection"
    output.mkdir()
    outbox = output / "opportunities.v1.jsonl"
    outbox.write_text("", encoding="utf-8")
    outbox.chmod(0o644)
    monkeypatch.setattr(
        run_kr_theme_projection,
        "project_kr_theme_opportunities",
        lambda *_args, **_kwargs: (),
    )

    run_kr_theme_projection.main(
        str(EXAMPLE / "projection-run.json"),
        str(database),
        str(output),
    )

    assert stat.S_IMODE(outbox.stat().st_mode) == 0o600


@pytest.mark.parametrize("fault", ["incomplete", "ambiguous", "missing_metric"])
def test_projection_cli_fails_closed_before_classification_append(
    tmp_path: Path,
    fault: str,
) -> None:
    fixture = _copy_fixture(tmp_path / "fixture")
    if fault == "incomplete":
        document = _json_object(fixture / "ingest-manifest.json")
        cycle = cast(dict[str, object], document["cycle"])
        coverage = cast(list[dict[str, object]], cycle["coverage"])
        coverage[0] = {
            "schema_version": 1,
            "source": "dart",
            "status": "failed",
            "record_count": 0,
            "failure_code": "synthetic_failure",
        }
        _write_json(fixture / "ingest-manifest.json", document)
    elif fault == "ambiguous":
        news = _json_object(fixture / "news-synthetic.json")
        news["title"] = "합성 반도체 우주 공동 촉매"
        _write_json(fixture / "news-synthetic.json", news)
        rules = _json_object(fixture / "keyword-rules.json")
        rule_rows = cast(list[dict[str, object]], rules["rules"])
        rule_rows.append(
            {
                "schema_version": 1,
                "theme_name": "우주항공",
                "keywords": ["우주"],
                "related_symbols": [
                    {
                        "schema_version": 1,
                        "symbol": "012345",
                        "relation": "direct_business",
                        "rationale": "합성 keyword rule 직접 연결",
                    }
                ],
            }
        )
        _write_json(fixture / "keyword-rules.json", rules)
    else:
        rules = _json_object(fixture / "keyword-rules.json")
        rule_rows = cast(list[dict[str, object]], rules["rules"])
        related = cast(list[dict[str, object]], rule_rows[0]["related_symbols"])
        related[0]["symbol"] = "012345"
        _write_json(fixture / "keyword-rules.json", rules)

    database = tmp_path / "kr-theme.sqlite3"
    run_kr_theme_ingest.main(
        str(fixture / "ingest-manifest.json"),
        str(database),
        str(tmp_path / "ingest"),
    )
    output = tmp_path / "projection"

    with pytest.raises(typer.BadParameter):
        run_kr_theme_projection.main(
            str(fixture / "projection-run.json"),
            str(database),
            str(output),
        )

    assert KrThemeStore(database).classifications() == ()
    assert not output.exists()


def test_projection_cli_corrupt_outbox_is_safe_and_restart_recoverable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "kr-theme.sqlite3"
    run_kr_theme_ingest.main(
        str(EXAMPLE / "ingest-manifest.json"),
        str(database),
        str(tmp_path / "ingest"),
    )
    output = tmp_path / "projection"
    output.mkdir()
    outbox = output / "opportunities.v1.jsonl"
    outbox.write_text("private-corrupt-outbox\n", encoding="utf-8")

    with pytest.raises(typer.BadParameter) as captured:
        run_kr_theme_projection.main(
            str(EXAMPLE / "projection-run.json"),
            str(database),
            str(output),
        )

    assert "private-corrupt-outbox" not in str(captured.value)
    assert stat.S_IMODE(outbox.stat().st_mode) == 0o600
    assert len(KrThemeStore(database).classifications()) == 1
    outbox.unlink()
    run_kr_theme_projection.main(
        str(EXAMPLE / "projection-run.json"),
        str(database),
        str(output),
    )
    assert len(outbox.read_text(encoding="utf-8").splitlines()) == 1


def test_projection_cli_invalid_manifest_fails_before_database_creation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "should-not-exist.sqlite3"
    output = tmp_path / "should-not-exist"

    with pytest.raises(typer.BadParameter):
        run_kr_theme_projection.main(
            str(tmp_path / "missing-run.json"),
            str(database),
            str(output),
        )

    assert not database.exists()
    assert not output.exists()


def _copy_fixture(destination: Path) -> Path:
    _ = shutil.copytree(EXAMPLE, destination)
    return destination


def _json_object(path: Path) -> dict[str, object]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

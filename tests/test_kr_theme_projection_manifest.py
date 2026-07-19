from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from trading_agent.kr_theme_projection_manifest import (
    KrThemeProjectionManifestError,
    load_kr_theme_projection_run,
)


def test_projection_run_manifest_loads_contained_keyword_rules(tmp_path: Path) -> None:
    manifest = _write_valid_run(tmp_path)

    loaded = load_kr_theme_projection_run(manifest)

    assert loaded.run.collection_cycle_id == "kr-theme-projection-001"
    assert loaded.run.validity_seconds == 600
    assert loaded.rules.classifier_version == "kr-keyword-synthetic-v1"
    assert loaded.rules.rules[0].theme_name == "반도체"


@pytest.mark.parametrize(
    "fault",
    [
        "traversal",
        "missing",
        "naive",
        "inverted",
        "validity",
        "unsafe_id",
        "extra",
    ],
)
def test_projection_run_manifest_rejects_invalid_contracts(
    tmp_path: Path,
    fault: str,
) -> None:
    _ = _write_rules(tmp_path / "rules.json")
    document = _run_document()
    if fault == "traversal":
        document["rules_path"] = "../rules.json"
    elif fault == "missing":
        document["rules_path"] = "missing.json"
    elif fault == "naive":
        document["classified_at"] = "2026-07-15T09:02:10"
    elif fault == "inverted":
        document["projected_at"] = "2026-07-15T09:02:00+09:00"
    elif fault == "validity":
        document["validity_seconds"] = 0
    elif fault == "unsafe_id":
        document["classification_run_id"] = "../unsafe"
    else:
        document["unexpected"] = True
    manifest = tmp_path / "run.json"
    manifest.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(KrThemeProjectionManifestError):
        _ = load_kr_theme_projection_run(manifest)


def test_projection_run_manifest_rejects_rule_symlink_escape(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "manifest"
    manifest_dir.mkdir()
    outside = tmp_path / "outside-rules.json"
    _ = _write_rules(outside)
    (manifest_dir / "rules.json").symlink_to(outside)
    manifest = manifest_dir / "run.json"
    manifest.write_text(
        json.dumps(_run_document(), ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(KrThemeProjectionManifestError):
        _ = load_kr_theme_projection_run(manifest)


def test_projection_run_manifest_rejects_noncanonical_rules(tmp_path: Path) -> None:
    rules_path = _write_rules(tmp_path / "rules.json")
    rules = cast(dict[str, object], json.loads(rules_path.read_text(encoding="utf-8")))
    rule_rows = cast(list[dict[str, object]], rules["rules"])
    rule_rows[0]["keywords"] = ["반도체", "공급망"]
    rules_path.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    manifest = tmp_path / "run.json"
    manifest.write_text(json.dumps(_run_document(), ensure_ascii=False), encoding="utf-8")

    with pytest.raises(KrThemeProjectionManifestError):
        _ = load_kr_theme_projection_run(manifest)


def _write_valid_run(directory: Path) -> Path:
    _ = _write_rules(directory / "rules.json")
    path = directory / "run.json"
    path.write_text(json.dumps(_run_document(), ensure_ascii=False), encoding="utf-8")
    return path


def _write_rules(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "classifier_version": "kr-keyword-synthetic-v1",
                "prompt_version": "no-prompt-v1",
                "rules": [
                    {
                        "schema_version": 1,
                        "theme_name": "반도체",
                        "keywords": ["공급망", "반도체"],
                        "related_symbols": [
                            {
                                "schema_version": 1,
                                "symbol": "005930",
                                "relation": "direct_business",
                                "rationale": "합성 keyword rule 직접 연결",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _run_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "collection_cycle_id": "kr-theme-projection-001",
        "rules_path": "rules.json",
        "classification_run_id": "kr-keyword-run-001",
        "classified_at": "2026-07-15T09:02:10+09:00",
        "projected_at": "2026-07-15T09:03:00+09:00",
        "validity_seconds": 600,
        "producer_strategy_version": "kr-theme-keyword-projection-v1-code-c80f7023e84a6369",
        "runtime_code_version": "kr-theme-fixture-code-v1",
    }

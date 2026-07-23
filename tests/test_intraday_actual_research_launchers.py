from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "script_name",
    (
        "run_intraday_actual_research.py",
        "run_planned_intraday_actual_research.py",
        "run_intraday_actual_research_audit.py",
        "run_intraday_research_dataset_catalog.py",
        "run_intraday_research_input_binding.py",
    ),
)
def test_intraday_research_standalone_launcher_declares_http_dependency(
    script_name: str,
) -> None:
    lines = (PROJECT / script_name).read_text(encoding="utf-8").splitlines()
    opening = lines.index("# /// script")
    closing = lines.index("# ///", opening + 1)
    metadata = tomllib.loads(
        "\n".join(line.removeprefix("# ") for line in lines[opening + 1 : closing])
    )

    assert "httpx2[http2,brotli,zstd]" in metadata["dependencies"]

from __future__ import annotations

import tomllib
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "run_kis_paper_scan.py"


def test_standalone_scan_launcher_declares_imported_analytics_dependencies() -> None:
    # Given: launchd may run the scan executable outside the project directory.
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()

    # When: the self-contained PEP 723 dependency declaration is parsed.
    assert lines[0] == "#!/usr/bin/env -S uv run --script"
    opening = lines.index("# /// script")
    closing = lines.index("# ///", opening + 1)
    metadata = tomllib.loads(
        "\n".join(line.removeprefix("# ") for line in lines[opening + 1 : closing])
    )

    # Then: imported analytics runtimes are pinned without relying on project cwd.
    assert "duckdb==1.5.4" in metadata["dependencies"]
    assert "pyarrow==25.0.0" in metadata["dependencies"]

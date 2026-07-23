from __future__ import annotations

import tomllib
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "run_adaptive_strategy_evaluation.py"


def test_standalone_adaptive_launcher_declares_transitive_http_dependency() -> None:
    # Given: post-session evaluation runs as a standalone PEP 723 child.
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()

    # When: the launcher's isolated dependency declaration is parsed.
    assert lines[0] == "#!/usr/bin/env -S uv run --script"
    opening = lines.index("# /// script")
    closing = lines.index("# ///", opening + 1)
    metadata = tomllib.loads(
        "\n".join(line.removeprefix("# ") for line in lines[opening + 1 : closing])
    )

    # Then: the transitive KIS import can resolve its HTTP runtime.
    assert "httpx2[http2,brotli,zstd]" in metadata["dependencies"]

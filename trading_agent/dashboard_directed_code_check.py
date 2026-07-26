from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from trading_agent.dashboard_directed_file_io import write_bytes_once


class DirectedCodeCheckError(RuntimeError):
    pass


def run_archive_safe_code_check(repository: Path, result_root: Path) -> tuple[str, str, str]:
    try:
        source_root = repository / "trading_agent"
        sources = tuple(sorted(source_root.rglob("*.py")))
        if not sources:
            raise DirectedCodeCheckError("directed_code_sources_missing")
        digest = hashlib.sha256()
        for path in sources:
            if path.is_symlink() or not path.is_file():
                raise DirectedCodeCheckError("directed_code_source_invalid")
            payload = path.read_bytes()
            _ = ast.parse(payload, filename=str(path))
            digest.update(path.relative_to(repository).as_posix().encode())
            digest.update(hashlib.sha256(payload).digest())
    except (OSError, SyntaxError, UnicodeError) as error:
        raise DirectedCodeCheckError("directed_code_check_failed") from error
    payload = json.dumps(
        {
            "files_checked": len(sources),
            "operation": "python_syntax_check",
            "source_tree_sha256": digest.hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    write_bytes_once(result_root / "code-check-receipt.json", payload)
    result_sha = hashlib.sha256(payload).hexdigest()
    return result_sha, result_sha, "allowlisted archive-safe syntax check completed"

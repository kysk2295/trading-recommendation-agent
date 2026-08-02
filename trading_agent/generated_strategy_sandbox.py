from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from trading_agent.generated_strategy_artifact import PublishedGeneratedStrategy
from trading_agent.generated_strategy_execution import (
    GeneratedStrategyExecutionError,
    GeneratedStrategyLimits,
)
from trading_agent.generated_strategy_runtime import (
    GeneratedStrategyRuntimeIdentity,
    require_generated_strategy_runtime,
)
from trading_agent.generated_strategy_session import GeneratedStrategySession

_RUNNER: Final = Path(__file__).with_name("generated_strategy_runner.py")


@dataclass(frozen=True, slots=True)
class GeneratedStrategySandbox:
    runtime: GeneratedStrategyRuntimeIdentity
    task_root: Path
    limits: GeneratedStrategyLimits

    def render_profile(
        self,
        published: PublishedGeneratedStrategy,
        session_root: Path,
    ) -> str:
        runtime_root = self.runtime.python_executable.parent.parent
        readable_roots = (Path("/System"), Path("/Library"), Path("/usr/lib"), Path("/usr/share"), runtime_root)
        source = published.source_path.resolve(strict=True)
        runner = _RUNNER.resolve(strict=True)
        task = session_root.resolve(strict=False)
        return "\n".join(
            (
                "(version 1)",
                "(deny default)",
                '(import "system.sb")',
                "(deny network*)",
                "(deny process-fork)",
                "(deny process-exec)",
                "(allow sysctl-read)",
                *(f"(allow file-read* (subpath {json.dumps(str(path))}))" for path in readable_roots),
                f"(allow file-read* (literal {json.dumps(str(source))}))",
                f"(allow file-read* (literal {json.dumps(str(runner))}))",
                f"(allow file-read* (literal {json.dumps(str(self.runtime.python_executable))}))",
                f"(allow process-exec (literal {json.dumps(str(self.runtime.python_executable))}))",
                f"(allow file-read* (subpath {json.dumps(str(task))}))",
                f"(allow file-write* (subpath {json.dumps(str(task))}))",
            )
        )

    def open_session(self, published: PublishedGeneratedStrategy) -> GeneratedStrategySession:
        try:
            _require_published(published, self.runtime)
            _ = require_generated_strategy_runtime(self.runtime)
            root = self.task_root.resolve(strict=False)
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            root.chmod(0o700)
            session_root = Path(tempfile.mkdtemp(prefix="generated-strategy-", dir=root))
            for child in (session_root / "home", session_root / "tmp"):
                child.mkdir(mode=0o700)
            profile = self.render_profile(published, session_root)
            return GeneratedStrategySession.start(
                published,
                self.runtime,
                self.limits,
                session_root,
                profile,
                _RUNNER.resolve(strict=True),
            )
        except GeneratedStrategyExecutionError:
            raise
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            raise GeneratedStrategyExecutionError("sandbox_preflight_failed") from None
def _require_published(
    published: PublishedGeneratedStrategy,
    runtime: GeneratedStrategyRuntimeIdentity,
) -> None:
    source = published.source_path
    metadata = source.lstat()
    if (
        source.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or published.artifact.payload.runtime != runtime
        or hashlib.sha256(source.read_bytes()).hexdigest() != published.artifact.payload.source_sha256
    ):
        raise GeneratedStrategyExecutionError("generated_artifact_invalid")

from __future__ import annotations

import os
import runpy
import sys
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

_DENIED_AUDIT_EVENTS = frozenset(
    {
        "ctypes.dlopen",
        "os.exec",
        "os.posix_spawn",
        "subprocess.Popen",
    }
)


class _HermesMain(Protocol):
    def __call__(self) -> int: ...


class _HermesDotenvLoader(Protocol):
    def __call__(
        self,
        *,
        hermes_home: str | os.PathLike[str] | None = None,
        project_env: str | os.PathLike[str] | None = None,
    ) -> list[Path]: ...


def _import_hermes_main() -> _HermesMain:
    environment_loader = cast(ModuleType, import_module("hermes_cli.env_loader"))
    original = cast(_HermesDotenvLoader, environment_loader.__dict__["load_hermes_dotenv"])

    def load_task_environment(
        *,
        hermes_home: str | os.PathLike[str] | None = None,
        project_env: str | os.PathLike[str] | None = None,
    ) -> list[Path]:
        del project_env
        return original(hermes_home=hermes_home)

    environment_loader.__dict__["load_hermes_dotenv"] = load_task_environment
    return cast(_HermesMain, import_module("hermes_cli.main").__dict__["main"])


def _deny_process_escape(
    event: str,
    args: tuple[str | bytes | int | float | bool | None, ...],
) -> None:
    del args
    if event in _DENIED_AUDIT_EVENTS or event.startswith("os.spawn"):
        raise PermissionError(f"audit event denied: {event}")


def _main(argv: tuple[str, ...]) -> int:
    if len(argv) != 3:
        return 64
    role, target, prompt = argv
    if target != os.environ.get("DASHBOARD_PINNED_TARGET"):
        return 65
    sys.addaudithook(_deny_process_escape)
    if role == "fixture-model":
        namespace = runpy.run_path(target, run_name="__main__")
        return int(namespace.get("EXIT_CODE", 0))
    if role == "research-broker":
        sys.path.insert(0, str(Path(target).parents[1]))
        sys.argv = [target, prompt]
        namespace = runpy.run_path(target, run_name="__main__")
        return int(namespace.get("EXIT_CODE", 0))
    if role not in {"hermes-model", "hermes-probe"}:
        return 66
    if role == "hermes-probe":
        print("Hermes Agent entrypoint verified")
        return 0
    agent_root = Path(target).parents[2]
    python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site_packages = agent_root / "venv" / "lib" / python_version / "site-packages"
    sys.path[:0] = [str(agent_root), str(site_packages)]
    sys.argv = [
        target,
        "--ignore-user-config",
        "--ignore-rules",
        "-t",
        "",
        "-z",
        prompt,
    ]
    main = _import_hermes_main()
    return int(main())


if __name__ == "__main__":
    raise SystemExit(_main(tuple(sys.argv[1:])))


__all__ = ()

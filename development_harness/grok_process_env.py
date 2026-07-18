"""Sanitize ambient process environment for harness Git and worker subprocesses."""

from __future__ import annotations

import os
from collections.abc import Mapping


def sanitize_git_routing_environ(*, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a copy of ``base`` (or ``os.environ``) without any ``GIT_*`` keys.

    Fail-closed: every environment key whose name starts with ``GIT_`` is removed.
    Harness Git invocations and worker/verification children must not inherit ambient
    Git routing or configuration variables from the parent shell. Unrelated environment
    keys are preserved.
    """

    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key.startswith("GIT_"):
            del env[key]
    return env

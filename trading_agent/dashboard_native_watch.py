from __future__ import annotations

import os
import select
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

from anyio.to_thread import run_sync

_VNODE_FLAGS: Final = (
    select.KQ_NOTE_WRITE
    | select.KQ_NOTE_EXTEND
    | select.KQ_NOTE_RENAME
    | select.KQ_NOTE_DELETE
    | select.KQ_NOTE_ATTRIB
)
_EVENT_FLAGS: Final = select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_CLEAR


async def watch_native_changes(
    *paths: Path,
    debounce: int,
    step: int,
) -> AsyncIterator[frozenset[Path]]:
    del step
    queue = select.kqueue()
    descriptors = tuple(os.open(path, os.O_RDONLY | os.O_CLOEXEC) for path in paths)
    watched = dict(zip(descriptors, paths, strict=True))
    try:
        queue.control(
            [
                select.kevent(
                    descriptor,
                    filter=select.KQ_FILTER_VNODE,
                    flags=_EVENT_FLAGS,
                    fflags=_VNODE_FLAGS,
                )
                for descriptor in descriptors
            ],
            0,
            0,
        )
        while True:
            yield await run_sync(
                _next_batch,
                queue,
                watched,
                debounce / 1_000,
                abandon_on_cancel=True,
            )
    finally:
        queue.close()
        for descriptor in descriptors:
            os.close(descriptor)


def _next_batch(
    queue: select.kqueue,
    watched: dict[int, Path],
    debounce_seconds: float,
) -> frozenset[Path]:
    events = queue.control(None, len(watched), None)
    changed = {watched[event.ident] for event in events}
    deadline = time.monotonic() + debounce_seconds
    while (remaining := deadline - time.monotonic()) > 0:
        events = queue.control(None, len(watched), remaining)
        if not events:
            break
        changed.update(watched[event.ident] for event in events)
    return frozenset(changed)


__all__ = ("watch_native_changes",)

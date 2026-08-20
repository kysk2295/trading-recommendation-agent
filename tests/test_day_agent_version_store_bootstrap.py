from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Final

import pytest

from tests.day_agent_version_learning_support import champion
from trading_agent.day_agent_version_models import DayAgentVersionStoreError
from trading_agent.day_agent_version_store import DayAgentVersionStore

_CRASH_BOOTSTRAP: Final = """
import os
import sys
from pathlib import Path
import trading_agent.day_agent_version_store_identity as identity
from trading_agent.day_agent_version_store import DayAgentVersionStore
boundary = sys.argv[2]
occurrence = int(sys.argv[3])
original = getattr(identity, boundary)
calls = 0
def crash_after(*args):
    global calls
    result = original(*args)
    calls += 1
    if calls == occurrence:
        os._exit(97)
    return result
setattr(identity, boundary, crash_after)
with DayAgentVersionStore(Path(sys.argv[1])).writer():
    pass
"""
_PAUSE_BOOTSTRAP: Final = """
import os
import sys
import time
from pathlib import Path
import trading_agent.day_agent_version_store_identity as identity
from trading_agent.day_agent_version_store import DayAgentVersionStore
original = identity._sync_identity_directory
calls = 0
def pause_after(descriptor):
    global calls
    original(descriptor)
    calls += 1
    if calls == 1:
        Path(sys.argv[2]).write_text('ready')
        os.chmod(sys.argv[2], 0o600)
        time.sleep(10)
identity._sync_identity_directory = pause_after
with DayAgentVersionStore(Path(sys.argv[1])).writer():
    pass
"""


@pytest.mark.parametrize(
    ("boundary", "occurrence"),
    tuple(
        (boundary, occurrence)
        for boundary in (
            "_write_identity_file",
            "_sync_identity_file",
            "_replace_identity_file",
            "_sync_identity_directory",
        )
        for occurrence in range(1, 5)
    ),
)
def test_crash_after_each_identity_publication_boundary_recovers_once(
    tmp_path: Path,
    boundary: str,
    occurrence: int,
) -> None:
    store = DayAgentVersionStore(tmp_path / f"{boundary}-{occurrence}" / "versions.sqlite3")
    completed = subprocess.run(
        (sys.executable, "-c", _CRASH_BOOTSTRAP, str(store.path), boundary, str(occurrence)),
        check=False,
    )
    assert completed.returncode == 97

    with store.writer() as writer:
        assert writer.register_initial_champion(champion())

    with store.writer() as writer:
        assert not writer.register_initial_champion(champion())
    assert store.reader().champion() == champion()


def test_marker_only_prepared_bootstrap_recovers_once(tmp_path: Path) -> None:
    store = DayAgentVersionStore(tmp_path / "store" / "versions.sqlite3")
    assert _crash_bootstrap(store.path, "_sync_identity_directory", 1) == 97
    anchor = tmp_path / ".store.versions.sqlite3.day-agent-version-store.json"
    marker = store.path.parent / ".versions.sqlite3.day-agent-version-store.json"
    marker.parent.mkdir(mode=0o700, exist_ok=True)
    anchor.replace(marker)

    with store.writer() as writer:
        assert writer.register_initial_champion(champion())
    assert store.reader().champion() == champion()


def test_legacy_anchor_only_crash_recovers_to_committed_identity(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    database = root / "versions.sqlite3"
    lock = root / "versions.sqlite3.writer.lock"
    database.touch(mode=0o600)
    lock.touch(mode=0o600)
    anchor = tmp_path / ".store.versions.sqlite3.day-agent-version-store.json"
    anchor.write_text(
        json.dumps(
            {
                "database": [database.stat().st_dev, database.stat().st_ino],
                "lock": [lock.stat().st_dev, lock.stat().st_ino],
                "parent": [root.stat().st_dev, root.stat().st_ino],
                "path": str(database),
                "token": "a" * 64,
                "version": 1,
            }
        )
    )
    anchor.chmod(0o600)
    store = DayAgentVersionStore(database)

    with store.writer() as writer:
        assert writer.register_initial_champion(champion())
    marker = root / ".versions.sqlite3.day-agent-version-store.json"
    assert json.loads(anchor.read_text())["phase"] == "committed"
    assert json.loads(marker.read_text())["phase"] == "committed"
    assert store.reader().champion() == champion()


def test_nonempty_one_sided_identity_fails_closed_without_repair(tmp_path: Path) -> None:
    store = DayAgentVersionStore(tmp_path / "store" / "versions.sqlite3")
    with store.writer() as writer:
        assert writer.register_initial_champion(champion())
    marker = store.path.parent / ".versions.sqlite3.day-agent-version-store.json"
    marker.unlink()

    with pytest.raises(DayAgentVersionStoreError, match="version_store_metadata_invalid"), store.writer():
        pass
    assert not marker.exists()


def test_mismatched_or_foreign_prepared_bootstrap_fails_closed(tmp_path: Path) -> None:
    mismatch = DayAgentVersionStore(tmp_path / "mismatch" / "store" / "versions.sqlite3")
    assert _crash_bootstrap(mismatch.path, "_sync_identity_directory", 1) == 97
    anchor = mismatch.path.parent.parent / ".store.versions.sqlite3.day-agent-version-store.json"
    marker = mismatch.path.parent / ".versions.sqlite3.day-agent-version-store.json"
    payload = json.loads(anchor.read_text())
    payload["token"] = "f" * 64
    marker.write_text(json.dumps(payload))
    marker.chmod(0o600)

    with pytest.raises(DayAgentVersionStoreError, match="version_store_metadata_invalid"), mismatch.writer():
        pass
    assert json.loads(marker.read_text())["token"] == "f" * 64

    foreign = DayAgentVersionStore(tmp_path / "foreign" / "store" / "versions.sqlite3")
    assert _crash_bootstrap(foreign.path, "_sync_identity_directory", 1) == 97
    foreign_file = foreign.path.parent / "versions.sqlite3.foreign"
    foreign_file.write_text("foreign")
    foreign_file.chmod(0o600)

    with pytest.raises(DayAgentVersionStoreError, match="version_store_metadata_invalid"), foreign.writer():
        pass
    assert foreign_file.read_text() == "foreign"


@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
def test_unsafe_prepared_temporary_identity_is_not_mutated(tmp_path: Path, link_kind: str) -> None:
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    target = tmp_path / "foreign-target"
    target.write_text("foreign")
    target.chmod(0o600)
    temporary = root / ".versions.sqlite3.day-agent-version-store.json.prepared.tmp"
    if link_kind == "symlink":
        temporary.symlink_to(target)
    else:
        os.link(target, temporary)

    with (
        pytest.raises(DayAgentVersionStoreError, match="version_store_metadata_invalid"),
        DayAgentVersionStore(root / "versions.sqlite3").writer(),
    ):
        pass
    assert target.read_text() == "foreign"
    assert temporary.exists()


def test_prepared_bootstrap_rejects_database_replacement(tmp_path: Path) -> None:
    store = DayAgentVersionStore(tmp_path / "store" / "versions.sqlite3")
    assert _crash_bootstrap(store.path, "_sync_identity_directory", 1) == 97
    replacement = store.path.parent / "replacement.sqlite3"
    replacement.touch(mode=0o600)
    os.replace(replacement, store.path)

    with pytest.raises(DayAgentVersionStoreError, match="version_store_metadata_invalid"), store.writer():
        pass


def test_concurrent_bootstrap_is_serialized_and_interrupted_owner_recovers(tmp_path: Path) -> None:
    store = DayAgentVersionStore(tmp_path / "store" / "versions.sqlite3")
    ready = tmp_path / "ready"
    process = subprocess.Popen((sys.executable, "-c", _PAUSE_BOOTSTRAP, str(store.path), str(ready)))
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        with pytest.raises(DayAgentVersionStoreError, match="version_store_writer_busy"), store.writer():
            pass
    finally:
        process.terminate()
        assert process.wait(timeout=5) == -15

    with store.writer() as writer:
        assert writer.register_initial_champion(champion())
    assert store.reader().champion() == champion()


def _crash_bootstrap(path: Path, boundary: str, occurrence: int) -> int:
    return subprocess.run(
        (sys.executable, "-c", _CRASH_BOOTSTRAP, str(path), boundary, str(occurrence)),
        check=False,
    ).returncode

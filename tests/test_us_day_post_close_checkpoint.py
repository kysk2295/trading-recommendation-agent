from pathlib import Path

import pytest

from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.us_day_post_close_checkpoint import (
    InvalidUsDayPostCloseCheckpointError,
    PaperFinalizedCheckpoint,
    PostCloseCheckpointIdentity,
    UsDayPostCloseCheckpointStore,
)


def _identity() -> PostCloseCheckpointIdentity:
    return PostCloseCheckpointIdentity(
        tick_id="a" * 64,
        session_id="XNYS-2026-08-20",
        source_sha256="b" * 64,
        champion_version_id="c" * 64,
    )


def test_divergent_post_close_checkpoint_fails_closed(tmp_path: Path) -> None:
    # Given: one immutable finalized-Paper checkpoint.
    store = UsDayPostCloseCheckpointStore(tmp_path / "checkpoints")
    identity = _identity()
    store.publish_paper(PaperFinalizedCheckpoint(identity=identity, paper_status="finalized"))

    # When / Then: a divergent payload for the same stage is rejected.
    with pytest.raises(InvalidUsDayPostCloseCheckpointError):
        store.publish_paper(PaperFinalizedCheckpoint(identity=identity, paper_status="different"))


def test_corrupt_post_close_checkpoint_fails_closed(tmp_path: Path) -> None:
    # Given: malformed content at the exact immutable stage path.
    store = UsDayPostCloseCheckpointStore(tmp_path / "checkpoints")
    identity = _identity()
    assert publish_private_immutable_text(store.root / identity.tick_id / "paper_finalized.json", "{}")

    # When / Then: the store refuses to infer or skip the corrupt stage.
    with pytest.raises(InvalidUsDayPostCloseCheckpointError):
        store.read(identity)

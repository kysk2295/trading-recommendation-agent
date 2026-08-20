from __future__ import annotations

import os
from pathlib import Path
from typing import override

from pydantic import ValidationError

from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.us_day_thesis_models import UsDayThesisChange, UsDayTradeThesis


class InvalidUsDayThesisStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "US day thesis store is invalid"


class UsDayThesisStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(os.path.abspath(root.expanduser())).resolve(strict=False)

    def publish_thesis(self, thesis: UsDayTradeThesis) -> bool:
        return self._publish(self.root / "theses" / f"{thesis.thesis_id}.json", thesis.model_dump_json())

    def publish_change(self, change: UsDayThesisChange) -> bool:
        thesis = self.thesis(change.thesis_id)
        prior = self.changes(change.thesis_id)
        existing = next((item for item in prior if item.event_id == change.event_id), None)
        if existing is not None:
            if existing != change:
                raise InvalidUsDayThesisStoreError
            return False
        latest_id = thesis.thesis_id if not prior else prior[-1].event_id
        latest_time = thesis.observed_at if not prior else prior[-1].occurred_at
        terminal = {"cancel_entry", "invalidate_logic", "close"}
        if (
            change.parent_event_id != latest_id
            or change.occurred_at < latest_time
            or (prior and prior[-1].kind.value in terminal)
        ):
            raise InvalidUsDayThesisStoreError
        return self._publish(
            self.root / "changes" / change.thesis_id / f"{change.event_id}.json",
            change.model_dump_json(),
        )

    def publish_terminal_card(self, thesis: UsDayTradeThesis, markdown: str) -> bool:
        if thesis.symbol is not None or not markdown.strip():
            raise InvalidUsDayThesisStoreError
        _ = self.thesis(thesis.thesis_id)
        return self._publish(self.root / "terminal_cards" / f"{thesis.thesis_id}.md", markdown)

    def thesis(self, thesis_id: str) -> UsDayTradeThesis:
        try:
            payload = read_private_text(self.root / "theses" / f"{thesis_id}.json")
            thesis = UsDayTradeThesis.model_validate_json(payload)
            if thesis.thesis_id != thesis_id:
                raise InvalidUsDayThesisStoreError
            return thesis
        except (InvalidPrivateImmutableFileError, OSError, ValidationError, ValueError):
            raise InvalidUsDayThesisStoreError from None

    def theses(self) -> tuple[UsDayTradeThesis, ...]:
        try:
            directory = self.root / "theses"
            if not directory.exists():
                return ()
            return tuple(self.thesis(path.stem) for path in sorted(directory.glob("*.json")))
        except (OSError, ValueError):
            raise InvalidUsDayThesisStoreError from None

    def changes(self, thesis_id: str) -> tuple[UsDayThesisChange, ...]:
        try:
            directory = self.root / "changes" / thesis_id
            if not directory.exists():
                return ()
            paths = tuple(sorted(directory.glob("*.json")))
            changes = tuple(UsDayThesisChange.model_validate_json(read_private_text(path)) for path in paths)
            if any(
                item.thesis_id != thesis_id or item.event_id != path.stem
                for item, path in zip(changes, paths, strict=True)
            ):
                raise InvalidUsDayThesisStoreError
            ordered: list[UsDayThesisChange] = []
            parent = thesis_id
            parent_time = self.thesis(thesis_id).observed_at
            terminal = {"cancel_entry", "invalidate_logic", "close"}
            remaining = {item.event_id: item for item in changes}
            while remaining:
                children = tuple(item for item in remaining.values() if item.parent_event_id == parent)
                if len(children) != 1 or (ordered and ordered[-1].kind.value in terminal):
                    raise InvalidUsDayThesisStoreError
                child = children[0]
                if child.occurred_at < parent_time:
                    raise InvalidUsDayThesisStoreError
                ordered.append(child)
                del remaining[child.event_id]
                parent = child.event_id
                parent_time = child.occurred_at
            return tuple(ordered)
        except (InvalidPrivateImmutableFileError, OSError, ValidationError, ValueError):
            raise InvalidUsDayThesisStoreError from None

    @staticmethod
    def _publish(path: Path, payload: str) -> bool:
        try:
            return publish_private_immutable_text(path, payload)
        except InvalidPrivateImmutableFileError:
            raise InvalidUsDayThesisStoreError from None


__all__ = ("InvalidUsDayThesisStoreError", "UsDayThesisStore")

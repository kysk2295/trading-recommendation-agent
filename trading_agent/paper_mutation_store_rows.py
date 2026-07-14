from __future__ import annotations

type MutationIntentRow = tuple[
    str,
    str,
    str,
    str,
    str | None,
    str | None,
    int | None,
    str,
    str,
    str | None,
    str | None,
    str | None,
    str | None,
]
type MutationEventRow = tuple[
    int,
    str,
    str,
    int,
    str,
    str,
    str | None,
    int | None,
    str | None,
    str,
]
type MutationEventValues = tuple[
    str,
    str,
    int,
    str,
    str,
    str | None,
    int | None,
    str | None,
    str,
]

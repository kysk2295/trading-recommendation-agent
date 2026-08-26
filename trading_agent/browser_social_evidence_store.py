from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from trading_agent.browser_social_evidence import (
    BrowserSocialEvidence,
    canonical_browser_social_evidence_json,
)
from trading_agent.browser_social_evidence_sqlite import (
    InvalidPrivateBrowserSocialEvidenceDatabaseError,
    PrivateBrowserSocialEvidenceDatabase,
    open_private_browser_social_evidence_database,
)

_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class InvalidBrowserSocialEvidenceStoreError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str = "browser_social_evidence_store_invalid") -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


class BrowserSocialEvidenceConflictError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self) -> None:
        self.reason = "browser_social_evidence_conflict"
        super().__init__(self.reason)

    def __str__(self) -> str:
        return self.reason


class _SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(ge=1, le=20)

    @field_validator("query")
    @classmethod
    def require_visible_query(cls, value: str) -> str:
        if not value.strip():
            raise InvalidBrowserSocialEvidenceStoreError()
        return value


class BrowserSocialEvidenceStore:
    def __init__(self, path: Path, *, owner_id: int | None = None) -> None:
        self.path = path.absolute()
        self._owner_id = os.geteuid() if owner_id is None else owner_id

    def append(self, evidence: BrowserSocialEvidence) -> bool:
        try:
            validated = BrowserSocialEvidence.model_validate(evidence.model_dump(mode="python"))
            payload = canonical_browser_social_evidence_json(validated)
            with open_private_browser_social_evidence_database(self.path, self._owner_id) as database:
                return _append(database, validated, payload)
        except BrowserSocialEvidenceConflictError:
            raise
        except (
            InvalidPrivateBrowserSocialEvidenceDatabaseError,
            OSError,
            sqlite3.Error,
            TypeError,
            ValidationError,
            ValueError,
        ):
            raise InvalidBrowserSocialEvidenceStoreError() from None

    def get(self, evidence_id: str) -> BrowserSocialEvidence | None:
        if _SHA256.fullmatch(evidence_id) is None:
            raise InvalidBrowserSocialEvidenceStoreError()
        try:
            with open_private_browser_social_evidence_database(self.path, self._owner_id) as database:
                row = database.connection.execute(
                    "SELECT * FROM browser_social_evidence WHERE evidence_id=?",
                    (evidence_id,),
                ).fetchone()
                return None if row is None else _decode_evidence(row)
        except (
            InvalidPrivateBrowserSocialEvidenceDatabaseError,
            OSError,
            sqlite3.Error,
            TypeError,
            ValidationError,
            ValueError,
        ):
            raise InvalidBrowserSocialEvidenceStoreError() from None

    def search(self, query: str, *, limit: int = 20) -> tuple[BrowserSocialEvidence, ...]:
        try:
            request = _SearchRequest(query=query, limit=limit)
            pattern = f"%{_escape_like(request.query)}%"
            with open_private_browser_social_evidence_database(self.path, self._owner_id) as database:
                rows = database.connection.execute(
                    """SELECT * FROM browser_social_evidence
                    WHERE title LIKE ? ESCAPE '\\' COLLATE NOCASE
                       OR author_label LIKE ? ESCAPE '\\' COLLATE NOCASE
                       OR excerpt LIKE ? ESCAPE '\\' COLLATE NOCASE
                       OR normalized_url LIKE ? ESCAPE '\\' COLLATE NOCASE
                    ORDER BY captured_at DESC, evidence_id LIMIT ?""",
                    (pattern, pattern, pattern, pattern, request.limit),
                ).fetchall()
                return tuple(_decode_evidence(row) for row in rows)
        except (
            InvalidBrowserSocialEvidenceStoreError,
            InvalidPrivateBrowserSocialEvidenceDatabaseError,
            OSError,
            sqlite3.Error,
            TypeError,
            ValidationError,
            ValueError,
        ):
            raise InvalidBrowserSocialEvidenceStoreError() from None


def _append(
    database: PrivateBrowserSocialEvidenceDatabase,
    evidence: BrowserSocialEvidence,
    payload: str,
) -> bool:
    connection = database.connection
    payload_sha256 = hashlib.sha256(payload.encode("ascii")).hexdigest()
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = connection.execute(
            "SELECT payload_sha256,payload_json FROM browser_social_evidence WHERE evidence_id=?",
            (evidence.evidence_id,),
        ).fetchone()
        if existing is not None:
            stored = _decode_payload(evidence.evidence_id, str(existing[0]), str(existing[1]))
            if stored == evidence and str(existing[1]) == payload and str(existing[0]) == payload_sha256:
                connection.commit()
                return False
            raise BrowserSocialEvidenceConflictError()
        connection.execute(
            "INSERT INTO browser_social_evidence VALUES (?,?,?,?,?,?,?,?)",
            (
                evidence.evidence_id,
                payload_sha256,
                payload,
                _canonical_timestamp(evidence.captured_at),
                evidence.title,
                evidence.author_label,
                evidence.excerpt,
                evidence.normalized_url,
            ),
        )
        connection.commit()
        return True
    except (BrowserSocialEvidenceConflictError, InvalidBrowserSocialEvidenceStoreError, sqlite3.Error):
        connection.rollback()
        raise


def _decode_evidence(row: tuple[str, ...]) -> BrowserSocialEvidence:
    if len(row) != 8:
        raise InvalidBrowserSocialEvidenceStoreError()
    evidence = _decode_payload(str(row[0]), str(row[1]), str(row[2]))
    projections = (
        _canonical_timestamp(evidence.captured_at),
        evidence.title,
        evidence.author_label,
        evidence.excerpt,
        evidence.normalized_url,
    )
    if tuple(str(value) for value in row[3:]) != projections:
        raise InvalidBrowserSocialEvidenceStoreError()
    return evidence


def _decode_payload(evidence_id: str, payload_sha256: str, payload: str) -> BrowserSocialEvidence:
    evidence = BrowserSocialEvidence.model_validate_json(payload)
    canonical = canonical_browser_social_evidence_json(evidence)
    if (
        evidence.evidence_id != evidence_id
        or canonical != payload
        or hashlib.sha256(payload.encode("ascii")).hexdigest() != payload_sha256
    ):
        raise InvalidBrowserSocialEvidenceStoreError()
    return evidence


def _escape_like(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _canonical_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = (
    "BrowserSocialEvidenceConflictError",
    "BrowserSocialEvidenceStore",
    "InvalidBrowserSocialEvidenceStoreError",
)

from __future__ import annotations

import datetime as dt
import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import final, override

from trading_agent.experiment_ledger_keys import (
    ExperimentTrialEventKey,
    ExperimentTrialRegistrationKey,
    HypothesisRegistrationKey,
    ResearchHypothesisCardKey,
    ResearchSourceKey,
    StrategyAuthorityBindingKey,
    StrategyLifecycleEventKey,
    StrategyVersionRegistrationKey,
    canonical_experiment_ledger_json,
    experiment_trial_event_key,
    experiment_trial_registration_key,
    hypothesis_registration_key,
    research_hypothesis_card_key,
    research_source_key,
    strategy_authority_binding_key,
    strategy_lifecycle_event_key,
    strategy_version_registration_key,
)
from trading_agent.experiment_ledger_models import (
    ExperimentTrialEvent,
    ExperimentTrialRegistration,
    HypothesisRegistration,
    ResearchHypothesisCard,
    ResearchSource,
    StrategyLifecycleEvent,
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
    StrategyVersionRegistration,
    TrialEventKind,
    lifecycle_state_rank,
)
from trading_agent.experiment_ledger_schema import (
    CREATE_EXPERIMENT_LEDGER_SCHEMA,
    CREATE_MULTI_MARKET_RESEARCH_SCHEMA_V4,
    CREATE_RESEARCH_SOURCE_LINEAGE_SCHEMA_V2,
    CREATE_STRATEGY_AUTHORITY_BINDING_SCHEMA_V3,
    EXPERIMENT_LEDGER_SCHEMA_VERSION,
    EXPERIMENT_LEDGER_SCHEMA_VERSION_V1,
    EXPERIMENT_LEDGER_SCHEMA_VERSION_V2,
    EXPERIMENT_LEDGER_SCHEMA_VERSION_V3,
)
from trading_agent.lifecycle_authority_policy import (
    InvalidLifecycleAuthorityError,
    require_new_champion_authority,
    require_persisted_champion_authority,
)
from trading_agent.multi_market_experiment_models import (
    MultiMarketHypothesisRegistration,
    MultiMarketStrategyVersionRegistration,
)
from trading_agent.multi_market_experiment_store import (
    InvalidMultiMarketExperimentSourceError,
    MultiMarketExperimentConflictError,
    StoredMultiMarketHypothesisRegistration,
    StoredMultiMarketStrategyVersionRegistration,
    read_multi_market_hypotheses,
    read_multi_market_strategy_versions,
    register_multi_market_hypothesis,
    register_multi_market_strategy_version,
)
from trading_agent.strategy_authority_models import StrategyAuthorityBinding

_V1_SCHEMA_OBJECTS = frozenset(
    {
        "hypotheses",
        "strategy_versions",
        "strategy_versions_by_lane",
        "experiment_trials",
        "experiment_trial_events",
        "experiment_trial_events_by_trial",
        "strategy_lifecycle_events",
        "strategy_lifecycle_events_by_version_date",
        "hypotheses_no_update",
        "hypotheses_no_delete",
        "strategy_versions_no_update",
        "strategy_versions_no_delete",
        "experiment_trials_no_update",
        "experiment_trials_no_delete",
        "experiment_trial_events_no_update",
        "experiment_trial_events_no_delete",
        "strategy_lifecycle_events_no_update",
        "strategy_lifecycle_events_no_delete",
    }
)

_V2_SCHEMA_OBJECTS = _V1_SCHEMA_OBJECTS | frozenset(
    {
        "research_sources",
        "research_hypothesis_cards",
        "research_sources_no_update",
        "research_sources_no_delete",
        "research_hypothesis_cards_no_update",
        "research_hypothesis_cards_no_delete",
    }
)

_V3_SCHEMA_OBJECTS = _V2_SCHEMA_OBJECTS | frozenset(
    {
        "strategy_authority_bindings",
        "strategy_authority_bindings_by_lane",
        "strategy_authority_bindings_no_update",
        "strategy_authority_bindings_no_delete",
    }
)


class ExperimentLedgerConflictError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "전역 experiment ledger immutable identity의 내용이 다릅니다"


class InvalidExperimentLedgerSourceError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "전역 experiment ledger의 immutable source 계약이 유효하지 않습니다"


class ExperimentLedgerWriterLeaseUnavailableError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "전역 experiment ledger single Writer lease를 획득하지 못했습니다"


class UnsupportedExperimentLedgerSchemaError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "지원하지 않는 전역 experiment ledger schema입니다"


class InactiveExperimentLedgerWriterError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "종료된 전역 experiment ledger Writer는 사용할 수 없습니다"


@dataclass(frozen=True, slots=True)
class StoredHypothesisRegistration:
    registration_key: HypothesisRegistrationKey
    registration: HypothesisRegistration


@dataclass(frozen=True, slots=True)
class StoredResearchSource:
    source_key: ResearchSourceKey
    source: ResearchSource


@dataclass(frozen=True, slots=True)
class StoredResearchHypothesisCard:
    card_key: ResearchHypothesisCardKey
    card: ResearchHypothesisCard


@dataclass(frozen=True, slots=True)
class StoredStrategyVersionRegistration:
    registration_key: StrategyVersionRegistrationKey
    registration: StrategyVersionRegistration


@dataclass(frozen=True, slots=True)
class StoredStrategyAuthorityBinding:
    binding_key: StrategyAuthorityBindingKey
    binding: StrategyAuthorityBinding


@dataclass(frozen=True, slots=True)
class StoredExperimentTrialRegistration:
    registration_key: ExperimentTrialRegistrationKey
    registration: ExperimentTrialRegistration


@dataclass(frozen=True, slots=True)
class StoredExperimentTrialEvent:
    event_key: ExperimentTrialEventKey
    event: ExperimentTrialEvent


@dataclass(frozen=True, slots=True)
class StoredStrategyLifecycleEvent:
    event_key: StrategyLifecycleEventKey
    event: StrategyLifecycleEvent


class ExperimentLedgerReader:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    def is_initialized(self) -> bool:
        if not self.path.is_file():
            return False
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
        finally:
            connection.close()
        return version == (EXPERIMENT_LEDGER_SCHEMA_VERSION,)

    def hypotheses(self) -> tuple[StoredHypothesisRegistration, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows: list[tuple[str, str, str, str, str]] = connection.execute(
                """SELECT registration_key, hypothesis_id, experiment_scope_key,
                lane_id, payload_json FROM hypotheses ORDER BY rowid"""
            ).fetchall()
        return tuple(_stored_hypothesis(row) for row in rows)

    def research_sources(self) -> tuple[StoredResearchSource, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows: list[tuple[str, str, str, str, str]] = connection.execute(
                """SELECT source_key, source_id, source_kind, source_url, payload_json
                FROM research_sources ORDER BY rowid"""
            ).fetchall()
        return tuple(_stored_research_source(row) for row in rows)

    def research_hypothesis_cards(self) -> tuple[StoredResearchHypothesisCard, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows: list[tuple[str, str, str]] = connection.execute(
                "SELECT card_key, hypothesis_id, payload_json FROM research_hypothesis_cards ORDER BY rowid"
            ).fetchall()
            cards = tuple(_stored_research_hypothesis_card(row) for row in rows)
            for card in cards:
                _require_valid_research_hypothesis_card_parent(connection, card)
        return cards

    def strategy_versions(self) -> tuple[StoredStrategyVersionRegistration, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows: list[tuple[str, str, str, str, str, str, str]] = connection.execute(
                """SELECT registration_key, strategy_version, strategy_id,
                hypothesis_id, experiment_scope_key, lane_id, payload_json
                FROM strategy_versions ORDER BY rowid"""
            ).fetchall()
        return tuple(_stored_strategy_version(row) for row in rows)

    def multi_market_hypotheses(
        self,
    ) -> tuple[StoredMultiMarketHypothesisRegistration, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            try:
                return read_multi_market_hypotheses(connection)
            except InvalidMultiMarketExperimentSourceError:
                raise InvalidExperimentLedgerSourceError from None

    def multi_market_strategy_versions(
        self,
    ) -> tuple[StoredMultiMarketStrategyVersionRegistration, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            try:
                return read_multi_market_strategy_versions(connection)
            except InvalidMultiMarketExperimentSourceError:
                raise InvalidExperimentLedgerSourceError from None

    def strategy_authority_bindings(self) -> tuple[StoredStrategyAuthorityBinding, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows: list[tuple[str, str, str, str, str, str, str, str, str]] = connection.execute(
                """SELECT binding_key, strategy_version, strategy_lane_id, market_id,
                agent_family, operating_mode, legacy_lane_id, bound_at, payload_json
                FROM strategy_authority_bindings ORDER BY rowid"""
            ).fetchall()
            bindings = tuple(_stored_strategy_authority_binding(row) for row in rows)
            for binding in bindings:
                _require_strategy_authority_parent(connection, binding)
        return bindings

    def trials(self) -> tuple[StoredExperimentTrialRegistration, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            rows: list[tuple[str, str, str, str, str, str]] = connection.execute(
                """SELECT registration_key, trial_id, strategy_version,
                experiment_scope_key, trial_kind, payload_json
                FROM experiment_trials ORDER BY rowid"""
            ).fetchall()
        return tuple(_stored_trial(row) for row in rows)

    def trial_events(self, trial_id: str) -> tuple[StoredExperimentTrialEvent, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            parent = _verified_trial_parent(connection, trial_id)
            events = _trial_events_by_id(connection, trial_id)
        _require_valid_trial_event_chain(parent, events)
        return events

    def lifecycle_events(
        self,
        strategy_version: str,
    ) -> tuple[StoredStrategyLifecycleEvent, ...]:
        if not self.path.is_file():
            return ()
        with self._reader_connection() as connection:
            version, hypothesis = _verified_lifecycle_parent(connection, strategy_version)
            events = _lifecycle_events_by_version(connection, strategy_version)
            authority = _strategy_authority_by_version(connection, strategy_version)
        _require_valid_lifecycle_chain(version, hypothesis, events)
        _require_persisted_lifecycle_authority(authority, events)
        return events

    def lifecycle_state(
        self,
        strategy_version: str,
        as_of_session_date: dt.date,
    ) -> StoredStrategyLifecycleEvent | None:
        effective = tuple(
            stored
            for stored in self.lifecycle_events(strategy_version)
            if stored.event.effective_session_date <= as_of_session_date
        )
        return None if not effective else effective[-1]

    @contextmanager
    def _reader_connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            _ = connection.execute("PRAGMA query_only = ON")
            _ = connection.execute("PRAGMA foreign_keys = ON")
            _require_current_schema(connection)
            yield connection
        finally:
            connection.close()


@final
class ExperimentLedgerStore(ExperimentLedgerReader):
    __slots__ = ()

    @contextmanager
    def writer(self) -> Iterator[ExperimentLedgerWriter]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{self.path}.writer.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ExperimentLedgerWriterLeaseUnavailableError from error
            connection = sqlite3.connect(self.path, timeout=0.0)
            os.chmod(self.path, 0o600)
            try:
                _prepare_writer_connection(connection)
                _ = connection.execute("BEGIN IMMEDIATE")
                writer = ExperimentLedgerWriter(connection)
                try:
                    yield writer
                except BaseException:
                    connection.rollback()
                    raise
                else:
                    connection.commit()
                finally:
                    writer._close()
            finally:
                connection.close()
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


@final
class ExperimentLedgerWriter:
    __slots__ = ("_active", "_connection")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._active = True

    def register_hypothesis(self, registration: HypothesisRegistration) -> bool:
        self._require_active()
        registration = _validated_hypothesis(registration)
        key = hypothesis_registration_key(registration)
        existing = _hypothesis_by_id(self._connection, registration.hypothesis_id)
        if existing is not None:
            if existing.registration_key == key and existing.registration == registration:
                return False
            raise ExperimentLedgerConflictError
        return self._insert_immutable(
            table="hypotheses",
            key_column="registration_key",
            key=key,
            insert_sql="INSERT INTO hypotheses VALUES (?, ?, ?, ?, ?)",
            insert_values=(
                key,
                registration.hypothesis_id,
                registration.experiment_scope_key,
                registration.primary_lane.value,
                canonical_experiment_ledger_json(registration),
            ),
        )

    def register_research_source(self, source: ResearchSource) -> bool:
        self._require_active()
        source = _validated_research_source(source)
        key = research_source_key(source)
        existing = _research_source_by_id(self._connection, source.source_id)
        if existing is not None:
            if existing.source_key == key and existing.source == source:
                return False
            raise ExperimentLedgerConflictError
        return self._insert_immutable(
            table="research_sources",
            key_column="source_key",
            key=key,
            insert_sql="INSERT INTO research_sources VALUES (?, ?, ?, ?, ?)",
            insert_values=(
                key,
                source.source_id,
                source.source_kind.value,
                source.source_url,
                canonical_experiment_ledger_json(source),
            ),
        )

    def register_research_hypothesis(self, card: ResearchHypothesisCard) -> bool:
        self._require_active()
        card = _validated_research_hypothesis_card(card)
        _require_research_hypothesis_sources(self._connection, card)
        key = research_hypothesis_card_key(card)
        existing = _research_hypothesis_card_by_hypothesis_id(
            self._connection,
            card.hypothesis.hypothesis_id,
        )
        if existing is not None:
            if existing.card_key == key and existing.card == card:
                return False
            raise ExperimentLedgerConflictError
        _ = self.register_hypothesis(card.hypothesis)
        return self._insert_immutable(
            table="research_hypothesis_cards",
            key_column="card_key",
            key=key,
            insert_sql="INSERT INTO research_hypothesis_cards VALUES (?, ?, ?)",
            insert_values=(
                key,
                card.hypothesis.hypothesis_id,
                canonical_experiment_ledger_json(card),
            ),
        )

    def register_strategy_version(self, registration: StrategyVersionRegistration) -> bool:
        self._require_active()
        registration = _validated_strategy_version(registration)
        parent = _hypothesis_by_id(self._connection, registration.hypothesis_id)
        if parent is None or not _version_matches_hypothesis(registration, parent.registration):
            raise InvalidExperimentLedgerSourceError
        key = strategy_version_registration_key(registration)
        existing = _strategy_version_by_id(self._connection, registration.strategy_version)
        if existing is not None:
            if existing.registration_key == key and existing.registration == registration:
                return False
            raise ExperimentLedgerConflictError
        return self._insert_immutable(
            table="strategy_versions",
            key_column="registration_key",
            key=key,
            insert_sql="INSERT INTO strategy_versions VALUES (?, ?, ?, ?, ?, ?, ?)",
            insert_values=(
                key,
                registration.strategy_version,
                registration.strategy_id,
                registration.hypothesis_id,
                registration.experiment_scope_key,
                registration.lane_id.value,
                canonical_experiment_ledger_json(registration),
            ),
        )

    def register_multi_market_hypothesis(
        self,
        registration: MultiMarketHypothesisRegistration,
    ) -> bool:
        self._require_active()
        try:
            return register_multi_market_hypothesis(self._connection, registration)
        except MultiMarketExperimentConflictError:
            raise ExperimentLedgerConflictError from None
        except InvalidMultiMarketExperimentSourceError:
            raise InvalidExperimentLedgerSourceError from None

    def register_multi_market_strategy_version(
        self,
        registration: MultiMarketStrategyVersionRegistration,
    ) -> bool:
        self._require_active()
        try:
            return register_multi_market_strategy_version(self._connection, registration)
        except MultiMarketExperimentConflictError:
            raise ExperimentLedgerConflictError from None
        except InvalidMultiMarketExperimentSourceError:
            raise InvalidExperimentLedgerSourceError from None

    def register_strategy_authority_binding(self, binding: StrategyAuthorityBinding) -> bool:
        self._require_active()
        binding = _validated_strategy_authority_binding(binding)
        parent = _strategy_version_by_id(self._connection, binding.strategy_version)
        stored = StoredStrategyAuthorityBinding(strategy_authority_binding_key(binding), binding)
        _require_strategy_authority_parent_for_binding(parent, stored)
        existing = _strategy_authority_by_version(self._connection, binding.strategy_version)
        if existing is not None:
            if existing == stored:
                return False
            raise ExperimentLedgerConflictError
        return self._insert_immutable(
            table="strategy_authority_bindings",
            key_column="binding_key",
            key=stored.binding_key,
            insert_sql="INSERT INTO strategy_authority_bindings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            insert_values=(
                stored.binding_key,
                binding.strategy_version,
                binding.strategy_lane.canonical_id,
                binding.strategy_lane.market_id.value,
                binding.strategy_lane.agent_family.value,
                binding.operating_mode.value,
                binding.legacy_lane_id.value,
                binding.bound_at.isoformat(),
                canonical_experiment_ledger_json(binding),
            ),
        )

    def register_trial(self, registration: ExperimentTrialRegistration) -> bool:
        self._require_active()
        registration = _validated_trial(registration)
        parent = _strategy_version_by_id(self._connection, registration.strategy_version)
        if parent is None or not _trial_matches_version(registration, parent.registration):
            raise InvalidExperimentLedgerSourceError
        key = experiment_trial_registration_key(registration)
        existing = _trial_by_id(self._connection, registration.trial_id)
        if existing is not None:
            if existing.registration_key == key and existing.registration == registration:
                return False
            raise ExperimentLedgerConflictError
        return self._insert_immutable(
            table="experiment_trials",
            key_column="registration_key",
            key=key,
            insert_sql="INSERT INTO experiment_trials VALUES (?, ?, ?, ?, ?, ?)",
            insert_values=(
                key,
                registration.trial_id,
                registration.strategy_version,
                registration.experiment_scope_key,
                registration.trial_kind.value,
                canonical_experiment_ledger_json(registration),
            ),
        )

    def append_trial_event(self, event: ExperimentTrialEvent) -> bool:
        self._require_active()
        event = _validated_trial_event(event)
        parent = _verified_trial_parent(self._connection, event.trial_id)
        events = _trial_events_by_id(self._connection, event.trial_id)
        _require_valid_trial_event_chain(parent, events)
        key = experiment_trial_event_key(event)
        existing = _trial_event_at_sequence(events, event.sequence)
        if existing is not None:
            if existing.event_key == key and existing.event == event:
                return False
            raise ExperimentLedgerConflictError
        _require_valid_trial_event_candidate(parent, events, event)
        return self._insert_immutable(
            table="experiment_trial_events",
            key_column="event_key",
            key=key,
            insert_sql="INSERT INTO experiment_trial_events VALUES (?, ?, ?, ?, ?, ?)",
            insert_values=(
                key,
                event.trial_id,
                event.sequence,
                event.event_kind.value,
                event.previous_event_key,
                canonical_experiment_ledger_json(event),
            ),
        )

    def append_lifecycle_event(self, event: StrategyLifecycleEvent) -> bool:
        self._require_active()
        event = _validated_lifecycle_event(event)
        version, hypothesis = _verified_lifecycle_parent(
            self._connection,
            event.strategy_version,
        )
        events = _lifecycle_events_by_version(self._connection, event.strategy_version)
        _require_valid_lifecycle_chain(version, hypothesis, events)
        authority = _strategy_authority_by_version(self._connection, event.strategy_version)
        _require_persisted_lifecycle_authority(authority, events)
        key = strategy_lifecycle_event_key(event)
        existing = _lifecycle_event_at_sequence(events, event.sequence)
        if existing is not None:
            if existing.event_key == key and existing.event == event:
                return False
            raise ExperimentLedgerConflictError
        _require_valid_lifecycle_candidate(version, hypothesis, events, event)
        _require_new_lifecycle_authority(authority, events, event)
        return self._insert_immutable(
            table="strategy_lifecycle_events",
            key_column="event_key",
            key=key,
            insert_sql="INSERT INTO strategy_lifecycle_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            insert_values=(
                key,
                event.strategy_version,
                event.sequence,
                event.event_kind.value,
                event.effective_session_date.isoformat(),
                event.previous_event_key,
                canonical_experiment_ledger_json(event),
            ),
        )

    def _insert_immutable(
        self,
        *,
        table: str,
        key_column: str,
        key: str,
        insert_sql: str,
        insert_values: tuple[object, ...],
    ) -> bool:
        collision = self._connection.execute(
            f"SELECT 1 FROM {table} WHERE {key_column} = ?",
            (key,),
        ).fetchone()
        if collision is not None:
            raise ExperimentLedgerConflictError
        try:
            _ = self._connection.execute(insert_sql, insert_values)
        except sqlite3.IntegrityError as error:
            raise ExperimentLedgerConflictError from error
        return True

    def _require_active(self) -> None:
        if not self._active:
            raise InactiveExperimentLedgerWriterError

    def _close(self) -> None:
        self._active = False


def _validated_hypothesis(registration: HypothesisRegistration) -> HypothesisRegistration:
    try:
        return HypothesisRegistration.model_validate(registration.model_dump(mode="python"))
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None


def _validated_research_source(source: ResearchSource) -> ResearchSource:
    try:
        return ResearchSource.model_validate(source.model_dump(mode="python"))
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None


def _validated_research_hypothesis_card(card: ResearchHypothesisCard) -> ResearchHypothesisCard:
    try:
        return ResearchHypothesisCard.model_validate(card.model_dump(mode="python"))
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None


def _validated_strategy_version(registration: StrategyVersionRegistration) -> StrategyVersionRegistration:
    try:
        return StrategyVersionRegistration.model_validate(registration.model_dump(mode="python"))
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None


def _validated_strategy_authority_binding(binding: StrategyAuthorityBinding) -> StrategyAuthorityBinding:
    try:
        return StrategyAuthorityBinding.model_validate(binding.model_dump(mode="python"))
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None


def _validated_trial(registration: ExperimentTrialRegistration) -> ExperimentTrialRegistration:
    try:
        return ExperimentTrialRegistration.model_validate(registration.model_dump(mode="python"))
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None


def _validated_trial_event(event: ExperimentTrialEvent) -> ExperimentTrialEvent:
    try:
        return ExperimentTrialEvent.model_validate(event.model_dump(mode="python"))
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None


def _validated_lifecycle_event(event: StrategyLifecycleEvent) -> StrategyLifecycleEvent:
    try:
        return StrategyLifecycleEvent.model_validate(event.model_dump(mode="python"))
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None


def _version_matches_hypothesis(
    version: StrategyVersionRegistration,
    hypothesis: HypothesisRegistration,
) -> bool:
    return (
        version.hypothesis_id == hypothesis.hypothesis_id
        and version.experiment_scope_key == hypothesis.experiment_scope_key
        and version.lane_id is hypothesis.primary_lane
        and version.source_registered_at == hypothesis.source_registered_at
        and version.ledger_recorded_at >= hypothesis.ledger_recorded_at
    )


def _trial_matches_version(
    trial: ExperimentTrialRegistration,
    version: StrategyVersionRegistration,
) -> bool:
    return (
        trial.strategy_version == version.strategy_version
        and trial.experiment_scope_key == version.experiment_scope_key
        and trial.experiment_scope.hypothesis_id == version.hypothesis_id
        and trial.experiment_scope.primary_lane is version.lane_id
        and trial.registered_at >= version.ledger_recorded_at
    )


def _hypothesis_by_id(
    connection: sqlite3.Connection,
    hypothesis_id: str,
) -> StoredHypothesisRegistration | None:
    row: tuple[str, str, str, str, str] | None = connection.execute(
        """SELECT registration_key, hypothesis_id, experiment_scope_key,
        lane_id, payload_json FROM hypotheses WHERE hypothesis_id = ?""",
        (hypothesis_id,),
    ).fetchone()
    return None if row is None else _stored_hypothesis(row)


def _research_source_by_id(
    connection: sqlite3.Connection,
    source_id: str,
) -> StoredResearchSource | None:
    row: tuple[str, str, str, str, str] | None = connection.execute(
        """SELECT source_key, source_id, source_kind, source_url, payload_json
        FROM research_sources WHERE source_id = ?""",
        (source_id,),
    ).fetchone()
    return None if row is None else _stored_research_source(row)


def _research_source_by_key(
    connection: sqlite3.Connection,
    source_key: str,
) -> StoredResearchSource | None:
    row: tuple[str, str, str, str, str] | None = connection.execute(
        """SELECT source_key, source_id, source_kind, source_url, payload_json
        FROM research_sources WHERE source_key = ?""",
        (source_key,),
    ).fetchone()
    return None if row is None else _stored_research_source(row)


def _research_hypothesis_card_by_hypothesis_id(
    connection: sqlite3.Connection,
    hypothesis_id: str,
) -> StoredResearchHypothesisCard | None:
    row: tuple[str, str, str] | None = connection.execute(
        "SELECT card_key, hypothesis_id, payload_json FROM research_hypothesis_cards WHERE hypothesis_id = ?",
        (hypothesis_id,),
    ).fetchone()
    return None if row is None else _stored_research_hypothesis_card(row)


def _strategy_version_by_id(
    connection: sqlite3.Connection,
    strategy_version: str,
) -> StoredStrategyVersionRegistration | None:
    row: tuple[str, str, str, str, str, str, str] | None = connection.execute(
        """SELECT registration_key, strategy_version, strategy_id,
        hypothesis_id, experiment_scope_key, lane_id, payload_json
        FROM strategy_versions WHERE strategy_version = ?""",
        (strategy_version,),
    ).fetchone()
    return None if row is None else _stored_strategy_version(row)


def _strategy_authority_by_version(
    connection: sqlite3.Connection,
    strategy_version: str,
) -> StoredStrategyAuthorityBinding | None:
    row: tuple[str, str, str, str, str, str, str, str, str] | None = connection.execute(
        """SELECT binding_key, strategy_version, strategy_lane_id, market_id,
        agent_family, operating_mode, legacy_lane_id, bound_at, payload_json
        FROM strategy_authority_bindings WHERE strategy_version = ?""",
        (strategy_version,),
    ).fetchone()
    return None if row is None else _stored_strategy_authority_binding(row)


def _require_strategy_authority_parent(
    connection: sqlite3.Connection,
    stored: StoredStrategyAuthorityBinding,
) -> None:
    parent = _strategy_version_by_id(connection, stored.binding.strategy_version)
    _require_strategy_authority_parent_for_binding(parent, stored)


def _require_strategy_authority_parent_for_binding(
    parent: StoredStrategyVersionRegistration | None,
    stored: StoredStrategyAuthorityBinding,
) -> None:
    binding = stored.binding
    if (
        parent is None
        or binding.strategy_lane.strategy_id != parent.registration.strategy_id
        or binding.legacy_lane_id is not parent.registration.lane_id
        or binding.bound_at < parent.registration.ledger_recorded_at
    ):
        raise InvalidExperimentLedgerSourceError


def _trial_by_id(
    connection: sqlite3.Connection,
    trial_id: str,
) -> StoredExperimentTrialRegistration | None:
    row: tuple[str, str, str, str, str, str] | None = connection.execute(
        """SELECT registration_key, trial_id, strategy_version,
        experiment_scope_key, trial_kind, payload_json
        FROM experiment_trials WHERE trial_id = ?""",
        (trial_id,),
    ).fetchone()
    return None if row is None else _stored_trial(row)


def _verified_trial_parent(
    connection: sqlite3.Connection,
    trial_id: str,
) -> StoredExperimentTrialRegistration | None:
    trial = _trial_by_id(connection, trial_id)
    if trial is None:
        return None
    version = _strategy_version_by_id(connection, trial.registration.strategy_version)
    if version is None or not _trial_matches_version(trial.registration, version.registration):
        raise InvalidExperimentLedgerSourceError
    hypothesis = _hypothesis_by_id(connection, version.registration.hypothesis_id)
    if hypothesis is None or not _version_matches_hypothesis(
        version.registration,
        hypothesis.registration,
    ):
        raise InvalidExperimentLedgerSourceError
    return trial


def _require_research_hypothesis_sources(
    connection: sqlite3.Connection,
    card: ResearchHypothesisCard,
) -> None:
    for source_key in card.research_source_keys:
        source = _research_source_by_key(connection, source_key)
        if source is None or source.source.ledger_recorded_at > card.hypothesis.source_registered_at:
            raise InvalidExperimentLedgerSourceError


def _require_valid_research_hypothesis_card_parent(
    connection: sqlite3.Connection,
    stored: StoredResearchHypothesisCard,
) -> None:
    hypothesis = _hypothesis_by_id(connection, stored.card.hypothesis.hypothesis_id)
    if hypothesis is None or hypothesis.registration != stored.card.hypothesis:
        raise InvalidExperimentLedgerSourceError
    _require_research_hypothesis_sources(connection, stored.card)


def _verified_lifecycle_parent(
    connection: sqlite3.Connection,
    strategy_version: str,
) -> tuple[StoredStrategyVersionRegistration | None, StoredHypothesisRegistration | None]:
    version = _strategy_version_by_id(connection, strategy_version)
    if version is None:
        return None, None
    hypothesis = _hypothesis_by_id(connection, version.registration.hypothesis_id)
    if hypothesis is None or not _version_matches_hypothesis(
        version.registration,
        hypothesis.registration,
    ):
        raise InvalidExperimentLedgerSourceError
    return version, hypothesis


def _trial_events_by_id(
    connection: sqlite3.Connection,
    trial_id: str,
) -> tuple[StoredExperimentTrialEvent, ...]:
    rows: list[tuple[str, str, int, str, str | None, str]] = connection.execute(
        """SELECT event_key, trial_id, sequence, event_kind,
        previous_event_key, payload_json FROM experiment_trial_events
        WHERE trial_id = ? ORDER BY sequence""",
        (trial_id,),
    ).fetchall()
    return tuple(_stored_trial_event(row) for row in rows)


def _lifecycle_events_by_version(
    connection: sqlite3.Connection,
    strategy_version: str,
) -> tuple[StoredStrategyLifecycleEvent, ...]:
    rows: list[tuple[str, str, int, str, str, str | None, str]] = connection.execute(
        """SELECT event_key, strategy_version, sequence, event_kind,
        effective_session_date, previous_event_key, payload_json
        FROM strategy_lifecycle_events WHERE strategy_version = ? ORDER BY sequence""",
        (strategy_version,),
    ).fetchall()
    return tuple(_stored_lifecycle_event(row) for row in rows)


def _trial_event_at_sequence(
    events: tuple[StoredExperimentTrialEvent, ...],
    sequence: int,
) -> StoredExperimentTrialEvent | None:
    return next((stored for stored in events if stored.event.sequence == sequence), None)


def _lifecycle_event_at_sequence(
    events: tuple[StoredStrategyLifecycleEvent, ...],
    sequence: int,
) -> StoredStrategyLifecycleEvent | None:
    return next((stored for stored in events if stored.event.sequence == sequence), None)


def _require_valid_trial_event_candidate(
    parent: StoredExperimentTrialRegistration | None,
    events: tuple[StoredExperimentTrialEvent, ...],
    event: ExperimentTrialEvent,
) -> None:
    if parent is None:
        raise InvalidExperimentLedgerSourceError
    _require_valid_trial_event_chain(
        parent,
        (*events, StoredExperimentTrialEvent(experiment_trial_event_key(event), event)),
    )


def _require_valid_trial_event_chain(
    parent: StoredExperimentTrialRegistration | None,
    events: tuple[StoredExperimentTrialEvent, ...],
) -> None:
    if parent is None:
        if events:
            raise InvalidExperimentLedgerSourceError
        return
    previous: StoredExperimentTrialEvent | None = None
    for expected_sequence, stored in enumerate(events, start=1):
        event = stored.event
        if (
            event.trial_id != parent.registration.trial_id
            or event.sequence != expected_sequence
            or event.occurred_at < parent.registration.registered_at
        ):
            raise InvalidExperimentLedgerSourceError
        if previous is None:
            if event.event_kind is not TrialEventKind.STARTED or event.previous_event_key is not None:
                raise InvalidExperimentLedgerSourceError
        elif (
            previous.event.event_kind is not TrialEventKind.STARTED
            or event.previous_event_key != previous.event_key
            or event.occurred_at < previous.event.occurred_at
        ):
            raise InvalidExperimentLedgerSourceError
        previous = stored


def _require_valid_lifecycle_candidate(
    version: StoredStrategyVersionRegistration | None,
    hypothesis: StoredHypothesisRegistration | None,
    events: tuple[StoredStrategyLifecycleEvent, ...],
    event: StrategyLifecycleEvent,
) -> None:
    if version is None or hypothesis is None:
        raise InvalidExperimentLedgerSourceError
    _require_valid_lifecycle_chain(
        version,
        hypothesis,
        (*events, StoredStrategyLifecycleEvent(strategy_lifecycle_event_key(event), event)),
    )


def _require_new_lifecycle_authority(
    authority: StoredStrategyAuthorityBinding | None,
    events: tuple[StoredStrategyLifecycleEvent, ...],
    event: StrategyLifecycleEvent,
) -> None:
    try:
        require_new_champion_authority(
            None if authority is None else authority.binding,
            tuple(stored.event for stored in events),
            event,
        )
    except InvalidLifecycleAuthorityError:
        raise InvalidExperimentLedgerSourceError from None


def _require_persisted_lifecycle_authority(
    authority: StoredStrategyAuthorityBinding | None,
    events: tuple[StoredStrategyLifecycleEvent, ...],
) -> None:
    try:
        require_persisted_champion_authority(
            None if authority is None else authority.binding,
            tuple(stored.event for stored in events),
        )
    except InvalidLifecycleAuthorityError:
        raise InvalidExperimentLedgerSourceError from None


def _require_valid_lifecycle_chain(
    version: StoredStrategyVersionRegistration | None,
    hypothesis: StoredHypothesisRegistration | None,
    events: tuple[StoredStrategyLifecycleEvent, ...],
) -> None:
    if version is None or hypothesis is None:
        if events:
            raise InvalidExperimentLedgerSourceError
        return
    previous: StoredStrategyLifecycleEvent | None = None
    for index, stored in enumerate(events):
        event = stored.event
        if (
            event.strategy_version != version.registration.strategy_version
            or event.sequence != index + 1
            or event.decided_at < version.registration.ledger_recorded_at
        ):
            raise InvalidExperimentLedgerSourceError
        if previous is None:
            if event.event_kind is not StrategyLifecycleEventKind.REGISTRATION:
                raise InvalidExperimentLedgerSourceError
            _require_valid_import_evidence(version, hypothesis, event)
        else:
            if (
                previous.event.to_state is StrategyLifecycleState.REJECTED
                or event.event_kind is not StrategyLifecycleEventKind.TRANSITION
                or event.previous_event_key != previous.event_key
                or event.from_state is not previous.event.to_state
                or event.decided_at < previous.event.decided_at
                or previous.event.effective_session_date > event.decision_session_date
            ):
                raise InvalidExperimentLedgerSourceError
            if previous.event.to_state is StrategyLifecycleState.SUSPENDED:
                _require_valid_suspended_recovery(events[:index], event.to_state)
        previous = stored


def _require_valid_import_evidence(
    version: StoredStrategyVersionRegistration,
    hypothesis: StoredHypothesisRegistration,
    event: StrategyLifecycleEvent,
) -> None:
    if event.to_state is StrategyLifecycleState.IDEA:
        return
    expected = tuple(
        sorted(
            (
                str(hypothesis.registration_key),
                version.registration.experiment_scope_key,
                str(version.registration_key),
            )
        )
    )
    if event.evidence_keys != expected:
        raise InvalidExperimentLedgerSourceError


def _require_valid_suspended_recovery(
    prior_events: tuple[StoredStrategyLifecycleEvent, ...],
    target: StrategyLifecycleState,
) -> None:
    if target is StrategyLifecycleState.REJECTED:
        return
    previous_active = next(
        (
            stored.event.to_state
            for stored in reversed(prior_events)
            if stored.event.to_state is not StrategyLifecycleState.SUSPENDED
        ),
        None,
    )
    if previous_active is None or lifecycle_state_rank(target) > lifecycle_state_rank(previous_active):
        raise InvalidExperimentLedgerSourceError


def _stored_hypothesis(row: tuple[str, str, str, str, str]) -> StoredHypothesisRegistration:
    key, hypothesis_id, scope_key, lane_id, payload = row
    try:
        registration = HypothesisRegistration.model_validate_json(payload)
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None
    typed_key = HypothesisRegistrationKey(key)
    if (
        typed_key != hypothesis_registration_key(registration)
        or hypothesis_id != registration.hypothesis_id
        or scope_key != registration.experiment_scope_key
        or lane_id != registration.primary_lane.value
    ):
        raise InvalidExperimentLedgerSourceError
    return StoredHypothesisRegistration(typed_key, registration)


def _stored_research_source(row: tuple[str, str, str, str, str]) -> StoredResearchSource:
    key, source_id, source_kind, source_url, payload = row
    try:
        source = ResearchSource.model_validate_json(payload)
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None
    typed_key = ResearchSourceKey(key)
    if (
        typed_key != research_source_key(source)
        or source_id != source.source_id
        or source_kind != source.source_kind.value
        or source_url != source.source_url
    ):
        raise InvalidExperimentLedgerSourceError
    return StoredResearchSource(typed_key, source)


def _stored_research_hypothesis_card(
    row: tuple[str, str, str],
) -> StoredResearchHypothesisCard:
    key, hypothesis_id, payload = row
    try:
        card = ResearchHypothesisCard.model_validate_json(payload)
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None
    typed_key = ResearchHypothesisCardKey(key)
    if typed_key != research_hypothesis_card_key(card) or hypothesis_id != card.hypothesis.hypothesis_id:
        raise InvalidExperimentLedgerSourceError
    return StoredResearchHypothesisCard(typed_key, card)


def _stored_strategy_version(
    row: tuple[str, str, str, str, str, str, str],
) -> StoredStrategyVersionRegistration:
    key, strategy_version, strategy_id, hypothesis_id, scope_key, lane_id, payload = row
    try:
        registration = StrategyVersionRegistration.model_validate_json(payload)
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None
    typed_key = StrategyVersionRegistrationKey(key)
    if (
        typed_key != strategy_version_registration_key(registration)
        or strategy_version != registration.strategy_version
        or strategy_id != registration.strategy_id
        or hypothesis_id != registration.hypothesis_id
        or scope_key != registration.experiment_scope_key
        or lane_id != registration.lane_id.value
    ):
        raise InvalidExperimentLedgerSourceError
    return StoredStrategyVersionRegistration(typed_key, registration)


def _stored_strategy_authority_binding(
    row: tuple[str, str, str, str, str, str, str, str, str],
) -> StoredStrategyAuthorityBinding:
    key, strategy_version, lane_id, market_id, family, mode, legacy_lane, bound_at, payload = row
    try:
        binding = StrategyAuthorityBinding.model_validate_json(payload)
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None
    typed_key = StrategyAuthorityBindingKey(key)
    if (
        typed_key != strategy_authority_binding_key(binding)
        or strategy_version != binding.strategy_version
        or lane_id != binding.strategy_lane.canonical_id
        or market_id != binding.strategy_lane.market_id.value
        or family != binding.strategy_lane.agent_family.value
        or mode != binding.operating_mode.value
        or legacy_lane != binding.legacy_lane_id.value
        or bound_at != binding.bound_at.isoformat()
    ):
        raise InvalidExperimentLedgerSourceError
    return StoredStrategyAuthorityBinding(typed_key, binding)


def _stored_trial(row: tuple[str, str, str, str, str, str]) -> StoredExperimentTrialRegistration:
    key, trial_id, strategy_version, scope_key, trial_kind, payload = row
    try:
        registration = ExperimentTrialRegistration.model_validate_json(payload)
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None
    typed_key = ExperimentTrialRegistrationKey(key)
    if (
        typed_key != experiment_trial_registration_key(registration)
        or trial_id != registration.trial_id
        or strategy_version != registration.strategy_version
        or scope_key != registration.experiment_scope_key
        or trial_kind != registration.trial_kind.value
    ):
        raise InvalidExperimentLedgerSourceError
    return StoredExperimentTrialRegistration(typed_key, registration)


def _stored_trial_event(
    row: tuple[str, str, int, str, str | None, str],
) -> StoredExperimentTrialEvent:
    key, trial_id, sequence, event_kind, previous_event_key, payload = row
    try:
        event = ExperimentTrialEvent.model_validate_json(payload)
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None
    typed_key = ExperimentTrialEventKey(key)
    if (
        typed_key != experiment_trial_event_key(event)
        or trial_id != event.trial_id
        or sequence != event.sequence
        or event_kind != event.event_kind.value
        or previous_event_key != event.previous_event_key
    ):
        raise InvalidExperimentLedgerSourceError
    return StoredExperimentTrialEvent(typed_key, event)


def _stored_lifecycle_event(
    row: tuple[str, str, int, str, str, str | None, str],
) -> StoredStrategyLifecycleEvent:
    (
        key,
        strategy_version,
        sequence,
        event_kind,
        effective_session_date,
        previous_event_key,
        payload,
    ) = row
    try:
        event = StrategyLifecycleEvent.model_validate_json(payload)
    except ValueError:
        raise InvalidExperimentLedgerSourceError from None
    typed_key = StrategyLifecycleEventKey(key)
    if (
        typed_key != strategy_lifecycle_event_key(event)
        or strategy_version != event.strategy_version
        or sequence != event.sequence
        or event_kind != event.event_kind.value
        or effective_session_date != event.effective_session_date.isoformat()
        or previous_event_key != event.previous_event_key
    ):
        raise InvalidExperimentLedgerSourceError
    return StoredStrategyLifecycleEvent(typed_key, event)


def _prepare_writer_connection(connection: sqlite3.Connection) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    _ = connection.execute("PRAGMA journal_mode = WAL").fetchone()
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    current = 0 if version is None else version[0]
    if current == 0:
        objects = tuple(
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        )
        if objects:
            raise UnsupportedExperimentLedgerSchemaError
        _apply_schema_transaction(
            connection,
            ddl=CREATE_EXPERIMENT_LEDGER_SCHEMA,
            version=EXPERIMENT_LEDGER_SCHEMA_VERSION,
        )
        return
    if current == EXPERIMENT_LEDGER_SCHEMA_VERSION_V1:
        _require_v1_schema(connection)
        _apply_schema_transaction(
            connection,
            ddl=CREATE_RESEARCH_SOURCE_LINEAGE_SCHEMA_V2
            + CREATE_STRATEGY_AUTHORITY_BINDING_SCHEMA_V3
            + CREATE_MULTI_MARKET_RESEARCH_SCHEMA_V4,
            version=EXPERIMENT_LEDGER_SCHEMA_VERSION,
        )
        return
    if current == EXPERIMENT_LEDGER_SCHEMA_VERSION_V2:
        _require_v2_schema(connection)
        _apply_schema_transaction(
            connection,
            ddl=CREATE_STRATEGY_AUTHORITY_BINDING_SCHEMA_V3 + CREATE_MULTI_MARKET_RESEARCH_SCHEMA_V4,
            version=EXPERIMENT_LEDGER_SCHEMA_VERSION,
        )
        return
    if current == EXPERIMENT_LEDGER_SCHEMA_VERSION_V3:
        _require_v3_schema(connection)
        _apply_schema_transaction(
            connection,
            ddl=CREATE_MULTI_MARKET_RESEARCH_SCHEMA_V4,
            version=EXPERIMENT_LEDGER_SCHEMA_VERSION,
        )
        return
    _require_current_schema(connection)


def _apply_schema_transaction(connection: sqlite3.Connection, *, ddl: str, version: int) -> None:
    try:
        connection.executescript(f"BEGIN IMMEDIATE;\n{ddl}\nPRAGMA user_version = {version};\nCOMMIT;")
    except sqlite3.Error:
        connection.rollback()
        raise


def _require_current_schema(connection: sqlite3.Connection) -> None:
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    if version != (EXPERIMENT_LEDGER_SCHEMA_VERSION,):
        raise UnsupportedExperimentLedgerSchemaError


def _require_v1_schema(connection: sqlite3.Connection) -> None:
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    if version != (EXPERIMENT_LEDGER_SCHEMA_VERSION_V1,):
        raise UnsupportedExperimentLedgerSchemaError
    actual_objects = frozenset(
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    )
    if actual_objects != _V1_SCHEMA_OBJECTS:
        raise UnsupportedExperimentLedgerSchemaError


def _require_v2_schema(connection: sqlite3.Connection) -> None:
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    if version != (EXPERIMENT_LEDGER_SCHEMA_VERSION_V2,):
        raise UnsupportedExperimentLedgerSchemaError
    actual_objects = frozenset(
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    )
    if actual_objects != _V2_SCHEMA_OBJECTS:
        raise UnsupportedExperimentLedgerSchemaError


def _require_v3_schema(connection: sqlite3.Connection) -> None:
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    if version != (EXPERIMENT_LEDGER_SCHEMA_VERSION_V3,):
        raise UnsupportedExperimentLedgerSchemaError
    actual_objects = frozenset(
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    )
    if actual_objects != _V3_SCHEMA_OBJECTS:
        raise UnsupportedExperimentLedgerSchemaError

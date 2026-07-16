from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import override

from pydantic import BaseModel, ValidationError

from trading_agent.signal_contract_models import (
    OpportunitySnapshot,
    SignalActionability,
)
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_quote_actionability import (
    QuoteActionabilityAssessment,
    UsQuoteSnapshot,
    quote_actionability_assessment_matches,
)


class ContractOutboxFormatError(ValueError):
    def __init__(self, path: Path, line_number: int) -> None:
        super().__init__(path, line_number)
        self.path = path
        self.line_number = line_number

    @override
    def __str__(self) -> str:
        return f"계약 outbox 형식이 유효하지 않습니다 ({self.path}:{self.line_number})"


class ContractOutboxConflictError(ValueError):
    def __init__(self, identity: str) -> None:
        super().__init__(identity)
        self.identity = identity

    @override
    def __str__(self) -> str:
        return f"동일 계약 ID에 서로 다른 내용이 있습니다: {self.identity}"


class InvalidQuoteActionabilityBatchError(ValueError):
    @override
    def __str__(self) -> str:
        return "invalid quote actionability batch"


def append_opportunity_snapshot(
    path: Path,
    snapshot: OpportunitySnapshot,
) -> bool:
    return _append_model(
        path,
        snapshot,
        model_type=OpportunitySnapshot,
        identity=lambda item: item.opportunity_id,
    )


def append_trade_signal_publication(
    path: Path,
    cards_dir: Path,
    publication: TradeSignalPublication,
) -> bool:
    if publication.signal.actionability is not SignalActionability.CONDITIONAL:
        raise ValueError("standalone signal writer accepts conditional publications only")
    model_plan, card_plans = _plan_trade_signal_publications(
        path,
        cards_dir,
        (publication,),
    )
    _commit_model_plan(model_plan)
    _commit_card_plans(card_plans)
    return model_plan.append_count == 1


def append_quote_actionability_batch(
    output: Path,
    snapshots: tuple[UsQuoteSnapshot, ...],
    derived_publications: tuple[TradeSignalPublication, ...],
    assessments: tuple[QuoteActionabilityAssessment, ...],
) -> tuple[int, int, int]:
    signal_path = output / "trade-signals.v1.jsonl"
    existing_publications: tuple[TradeSignalPublication, ...] = ()
    if snapshots or derived_publications or assessments:
        _, existing_publications = _read_models(
            signal_path,
            model_type=TradeSignalPublication,
            identity=lambda item: item.signal.signal_id,
        )
    publication_by_id = {
        publication.signal.signal_id: publication
        for publication in existing_publications
    }
    _validate_quote_actionability_batch(
        snapshots,
        derived_publications,
        assessments,
        publication_by_id,
    )
    snapshot_plan = _plan_models(
        output / "us-quote-snapshots.v2.jsonl",
        snapshots,
        model_type=UsQuoteSnapshot,
        identity=lambda item: item.quote_id,
    )
    signal_plan, card_plans = _plan_trade_signal_publications(
        signal_path,
        output / "trade-signal-cards-ko",
        derived_publications,
    )
    assessment_plan = _plan_models(
        output / "quote-actionability-assessments.v2.jsonl",
        assessments,
        model_type=QuoteActionabilityAssessment,
        identity=lambda item: item.assessment_id,
    )

    _commit_model_plan(snapshot_plan)
    _commit_model_plan(signal_plan)
    _commit_card_plans(card_plans)
    _commit_model_plan(assessment_plan)
    return (
        snapshot_plan.append_count,
        signal_plan.append_count,
        assessment_plan.append_count,
    )


def _validate_quote_actionability_batch(
    snapshots: tuple[UsQuoteSnapshot, ...],
    derived_publications: tuple[TradeSignalPublication, ...],
    assessments: tuple[QuoteActionabilityAssessment, ...],
    publication_by_id: Mapping[str, TradeSignalPublication],
) -> None:
    snapshot_ids = tuple(snapshot.quote_id for snapshot in snapshots)
    derived_ids = tuple(
        publication.signal.signal_id for publication in derived_publications
    )
    assessment_ids = tuple(
        assessment.assessment_id for assessment in assessments
    )
    base_signal_ids = tuple(
        assessment.base_signal_id for assessment in assessments
    )
    assessment_quote_ids = tuple(
        assessment.quote_id
        for assessment in assessments
        if assessment.quote_id is not None
    )
    assessment_derived_ids = tuple(
        assessment.derived_signal_id
        for assessment in assessments
        if assessment.derived_signal_id is not None
    )
    invalid_geometry = (
        len(snapshot_ids) != len(set(snapshot_ids))
        or len(derived_ids) != len(set(derived_ids))
        or len(assessment_ids) != len(set(assessment_ids))
        or len(base_signal_ids) != len(set(base_signal_ids))
        or len({assessment.scan_started_at for assessment in assessments}) > 1
        or set(snapshot_ids) != set(assessment_quote_ids)
        or set(derived_ids) != set(assessment_derived_ids)
        or len(assessment_derived_ids) != len(set(assessment_derived_ids))
    )
    if invalid_geometry:
        raise InvalidQuoteActionabilityBatchError

    base_by_id: dict[str, TradeSignalPublication] = {}
    for assessment in assessments:
        base = publication_by_id.get(assessment.base_signal_id)
        if (
            base is None
            or base.signal.actionability is not SignalActionability.CONDITIONAL
            or base.signal.quote_validation is not None
        ):
            raise InvalidQuoteActionabilityBatchError
        base_by_id[assessment.base_signal_id] = base

    snapshot_by_id = {snapshot.quote_id: snapshot for snapshot in snapshots}
    derived_by_id = {
        publication.signal.signal_id: publication
        for publication in derived_publications
    }
    for assessment in assessments:
        base = base_by_id[assessment.base_signal_id]
        snapshot = (
            None
            if assessment.quote_id is None
            else snapshot_by_id.get(assessment.quote_id)
        )
        derived = (
            None
            if assessment.derived_signal_id is None
            else derived_by_id.get(assessment.derived_signal_id)
        )
        if not quote_actionability_assessment_matches(
            base,
            snapshot,
            assessment,
            derived,
        ):
            raise InvalidQuoteActionabilityBatchError


@dataclass(frozen=True, slots=True)
class _ModelAppendPlan:
    path: Path
    existing_content: str
    encoded_records: tuple[str, ...]

    @property
    def append_count(self) -> int:
        return len(self.encoded_records)


@dataclass(frozen=True, slots=True)
class _CardWritePlan:
    path: Path
    content: str


def _plan_models[ModelT: BaseModel](
    path: Path,
    values: tuple[ModelT, ...],
    *,
    model_type: type[ModelT],
    identity: Callable[[ModelT], str],
) -> _ModelAppendPlan:
    if not values:
        return _ModelAppendPlan(path, "", ())
    content, existing = _read_models(
        path,
        model_type=model_type,
        identity=identity,
    )
    payload_by_identity = {
        identity(item): item.model_dump(mode="json") for item in existing
    }
    encoded_records: list[str] = []
    for value in values:
        value_identity = identity(value)
        value_payload = value.model_dump(mode="json")
        existing_payload = payload_by_identity.get(value_identity)
        if existing_payload is not None:
            if existing_payload != value_payload:
                raise ContractOutboxConflictError(value_identity)
            continue
        payload_by_identity[value_identity] = value_payload
        encoded_records.append(
            json.dumps(
                value_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return _ModelAppendPlan(path, content, tuple(encoded_records))


def _plan_trade_signal_publications(
    path: Path,
    cards_dir: Path,
    publications: tuple[TradeSignalPublication, ...],
) -> tuple[_ModelAppendPlan, tuple[_CardWritePlan, ...]]:
    if publications:
        _validate_directory_target(
            cards_dir,
            identity=publications[0].signal.signal_id,
        )
    model_plan = _plan_models(
        path,
        publications,
        model_type=TradeSignalPublication,
        identity=lambda item: item.signal.signal_id,
    )
    planned_cards: dict[Path, str] = {}
    for publication in publications:
        signal_id = publication.signal.signal_id
        card_path = _signal_card_path(cards_dir, signal_id)
        card = _signal_card(publication)
        pending = planned_cards.get(card_path)
        if pending is not None and pending != card:
            raise ContractOutboxConflictError(signal_id)
        if card_path.exists():
            if (
                not card_path.is_file()
                or card_path.read_text(encoding="utf-8") != card
            ):
                raise ContractOutboxConflictError(signal_id)
            continue
        planned_cards[card_path] = card
    return model_plan, tuple(
        _CardWritePlan(path=card_path, content=planned_cards[card_path])
        for card_path in sorted(planned_cards)
    )


def _validate_directory_target(path: Path, *, identity: str) -> None:
    current = path
    while not current.exists():
        if current.is_symlink():
            raise ContractOutboxConflictError(identity)
        parent = current.parent
        if parent == current:
            return
        current = parent
    if not current.is_dir():
        raise ContractOutboxConflictError(identity)


def _commit_model_plan(plan: _ModelAppendPlan) -> None:
    if not plan.encoded_records:
        return
    plan.path.parent.mkdir(parents=True, exist_ok=True)
    separator = (
        "\n"
        if plan.existing_content and not plan.existing_content.endswith("\n")
        else ""
    )
    payload = separator + "\n".join(plan.encoded_records) + "\n"
    with plan.path.open("a", encoding="utf-8") as handle:
        _ = handle.write(payload)


def _commit_card_plans(plans: tuple[_CardWritePlan, ...]) -> None:
    for plan in plans:
        plan.path.parent.mkdir(parents=True, exist_ok=True)
        _ = plan.path.write_text(plan.content, encoding="utf-8")


def _append_model[ModelT: BaseModel](
    path: Path,
    value: ModelT,
    *,
    model_type: type[ModelT],
    identity: Callable[[ModelT], str],
) -> bool:
    content, existing = _read_models(path, model_type=model_type, identity=identity)
    new_identity = identity(value)
    new_payload = value.model_dump(mode="json")
    for item in existing:
        if identity(item) != new_identity:
            continue
        if item.model_dump(mode="json") == new_payload:
            return False
        raise ContractOutboxConflictError(new_identity)

    encoded = json.dumps(
        new_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    separator = "\n" if content and not content.endswith("\n") else ""
    with path.open("a", encoding="utf-8") as handle:
        _ = handle.write(f"{separator}{encoded}\n")
    return True


def _read_models[ModelT: BaseModel](
    path: Path,
    *,
    model_type: type[ModelT],
    identity: Callable[[ModelT], str],
) -> tuple[str, tuple[ModelT, ...]]:
    if not path.exists():
        return "", ()
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ContractOutboxFormatError(path, 0) from error

    models: list[ModelT] = []
    identities: set[str] = set()
    for line_number, line in enumerate(content.splitlines(), start=1):
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise TypeError
            model = model_type.model_validate(raw)
            model_identity = identity(model)
        except (json.JSONDecodeError, TypeError, ValidationError) as error:
            raise ContractOutboxFormatError(path, line_number) from error
        if model_identity in identities:
            raise ContractOutboxFormatError(path, line_number)
        identities.add(model_identity)
        models.append(model)
    return content, tuple(models)


def _signal_card_path(cards_dir: Path, signal_id: str) -> Path:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", signal_id).strip("._-")[:48]
    if not stem:
        stem = "signal"
    digest = hashlib.sha256(signal_id.encode("utf-8")).hexdigest()[:12]
    return cards_dir / f"{stem}-{digest}.ko.md"


def _signal_card(publication: TradeSignalPublication) -> str:
    signal = publication.signal
    title = (
        "미국 주식 조건부 트레이딩 신호"
        if signal.actionability is SignalActionability.CONDITIONAL
        else "미국 주식 현재 호가 검증 트레이딩 신호"
    )
    actionability = (
        "조건부 (현재 호가 미검증)"
        if signal.actionability is SignalActionability.CONDITIONAL
        else "현재 호가 검증"
    )
    targets = " / ".join(
        f"{target.label} {_decimal_text(target.price)}"
        for target in signal.targets
    )
    quote = signal.quote_validation
    quote_lines = (
        ()
        if quote is None
        else (
            f"- 호가 관측 시각: {quote.observed_at.isoformat()}",
            f"- 현재 bid/ask: {_decimal_text(quote.bid)} / {_decimal_text(quote.ask)}",
            f"- spread: {_decimal_text(quote.spread_bps)} bp",
            f"- 트리거 상태: {'도달' if quote.ask >= signal.entry_price else '대기'}",
        )
    )
    lines = (
        f"# {title}",
        "",
        "> 연구 및 Paper forward-validation 후보이며 확정수익이나 자동주문이 아닙니다.",
        "",
        f"- 시장: {signal.strategy_lane.market_id.value}",
        f"- 전략: {signal.strategy_lane.canonical_id}",
        f"- 전략 버전: {signal.producer_strategy_version}",
        f"- 신호 관측 시각: {signal.observed_at.isoformat()}",
        f"- 발행 시각: {publication.published_at.isoformat()}",
        f"- 유효 종료: {signal.valid_until.isoformat()}",
        f"- 종목: {signal.symbol}",
        f"- 실행 가능성: {actionability}",
        *quote_lines,
        f"- 조건부 진입: {signal.entry_type.value} {_decimal_text(signal.entry_price)}",
        f"- 손절: {_decimal_text(signal.stop_price)}",
        f"- 목표: {targets}",
        f"- 무효화: {signal.invalidation_rule}",
        f"- 근거: {signal.rationale}",
        f"- 기회 ID: {signal.opportunity_id or '없음'}",
        "",
    )
    return "\n".join(lines)


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")

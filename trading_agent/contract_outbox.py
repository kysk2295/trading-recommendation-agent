from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import override

from pydantic import BaseModel, ValidationError

from trading_agent.signal_contract_models import (
    OpportunitySnapshot,
    SignalActionability,
)
from trading_agent.trade_signal_publication import TradeSignalPublication


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
    card_path = _signal_card_path(cards_dir, publication.signal.signal_id)
    card = _signal_card(publication)
    if card_path.exists() and (
        not card_path.is_file() or card_path.read_text(encoding="utf-8") != card
    ):
        raise ContractOutboxConflictError(publication.signal.signal_id)

    appended = _append_model(
        path,
        publication,
        model_type=TradeSignalPublication,
        identity=lambda item: item.signal.signal_id,
    )
    if not card_path.exists():
        cards_dir.mkdir(parents=True, exist_ok=True)
        _ = card_path.write_text(card, encoding="utf-8")
    return appended


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

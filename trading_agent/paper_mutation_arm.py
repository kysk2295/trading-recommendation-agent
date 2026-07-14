from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, override

PAPER_MUTATION_ARM_VALUE: Final = "ARM_ALPACA_PAPER_ONLY"


class InvalidPaperMutationArmError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca Paper mutation arm 값이 정확하지 않습니다"


@dataclass(frozen=True, slots=True)
class PaperMutationArm:
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if self.value != PAPER_MUTATION_ARM_VALUE:
            raise InvalidPaperMutationArmError


def require_paper_mutation_arm(value: object) -> PaperMutationArm:
    if not isinstance(value, PaperMutationArm) or value.value != PAPER_MUTATION_ARM_VALUE:
        raise InvalidPaperMutationArmError
    return value

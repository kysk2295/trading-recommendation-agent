from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.kr_loop_engineer_models import KrLoopCandidateSnapshot, KrLoopCandidateState


@dataclass(frozen=True, slots=True)
class KrLoopHermesFields:
    kind: HermesDeliveryKind
    status: str
    evidence_refs: tuple[str, ...]
    rendered_text: str


def kr_loop_hermes_fields(snapshot: KrLoopCandidateSnapshot) -> KrLoopHermesFields:
    match snapshot.state:
        case KrLoopCandidateState.DETECTED:
            text = "[Loop Engineer] 반복 실패 증거를 접수해 격리 코드 challenger 작업을 대기 중입니다."
            kind = HermesDeliveryKind.RESEARCH
        case KrLoopCandidateState.CANDIDATE_READY:
            text = "[Loop Engineer] 격리 코드 challenger가 생성되어 독립 검증을 대기 중입니다."
            kind = HermesDeliveryKind.RESEARCH
        case KrLoopCandidateState.SHADOWING:
            text = (
                "[Loop Engineer] 독립 검증을 통과해 미래 Shadow를 수행 중입니다. "
                f"세션={len(snapshot.shadow_receipts)}/2."
            )
            kind = HermesDeliveryKind.RESEARCH
        case KrLoopCandidateState.PROMOTED:
            text = "[Loop Engineer] 미래 Shadow 기준을 통과해 모의운영 release로 자동 승격했습니다."
            kind = HermesDeliveryKind.RESEARCH
        case KrLoopCandidateState.REJECTED:
            text = "[Loop Engineer] 검증 기준을 통과하지 못해 코드 challenger를 폐기했습니다."
            kind = HermesDeliveryKind.INCIDENT
        case KrLoopCandidateState.ROLLED_BACK:
            text = "[Loop Engineer] 운영 이상을 감지해 직전 모의운영 release로 자동 복귀했습니다."
            kind = HermesDeliveryKind.INCIDENT
        case unreachable:
            assert_never(unreachable)
    refs = {
        snapshot.bundle_id,
        *(item for item in (snapshot.patch_sha256, snapshot.verification_sha256) if item is not None),
        *(ref for receipt in snapshot.shadow_receipts for ref in receipt.evidence_refs),
        *(ref for receipt in snapshot.health_receipts for ref in receipt.evidence_refs),
    }
    return KrLoopHermesFields(
        kind=kind,
        status=f"loop_{snapshot.state.value}",
        evidence_refs=tuple(sorted(refs))[:32],
        rendered_text=f"{text} 모의운영 전용, 실거래 권한=false.",
    )


__all__ = ("KrLoopHermesFields", "kr_loop_hermes_fields")

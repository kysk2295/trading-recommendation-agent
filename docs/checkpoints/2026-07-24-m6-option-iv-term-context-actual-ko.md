# M6 actual option IV term context

- 구현 커밋: `2d6ba51a91314e69d3e93cd64cbc7621d992dc14`
- source: actual AAPL indicative call READY term structure
- source market date: 2026-07-23
- 권한: Derivatives Research, query-only·shadow-only

## 제품 결과

content-addressed READY term structure를 query-only로 읽어 가장 가까운 만기와 가장 먼
만기의 all-strike median IV 차이를 계산하는 파생상품 context vertical을 추가했다.

```text
READY multi-expiration option term structure
-> exact semantic ID + source file SHA
-> 단일 call/put right와 고유 만기 검증
-> near/far median IV
-> front_minus_back_iv
-> front_premium | flat | back_premium
-> content-addressed mode-600 context
```

분류는 임의 예측 threshold를 쓰지 않는다. near IV가 far IV보다 높으면
`front_premium`, 낮으면 `back_premium`, 정확히 같으면 `flat`인 기술적 상태 설명만
만든다. 방향, 진입가, 손절, 목표, 수량 또는 주문 권한은 없다.

renamed/non-content-addressed source, READY가 아닌 term structure, expiration보다 surface가
많은 입력과 call/put 혼합은 output 생성 전에 차단한다.

## actual 결과

기존 actual AAPL 2026-07-24/31 call term structure를 입력했다.

- source expirations: 2
- near/far days to expiry: 1/8
- near/far median IV: 0.4052/0.3996
- front minus back IV: 0.0056
- state: `front_premium`
- context ID:
  `128432efcf47e3ceb5b8c7deee2b3f548630ffe4c1dfae36b2bbb451a0ebcb8c`
- artifact SHA-256:
  `f1e3f0fed9be25b824204e53a41d7ef6f75210c67cf1d3ac0ed35049bab62175`
- artifact/report mode: 600/600
- 최초/replay artifact: yes/no
- network, broker, account, order mutation: 0

## 검증

- failing-first: missing CLI에서 happy/replay 2개 실패 확인
- focused: 4 passed
- 전체 pytest: 3637 passed
- Ruff: 통과
- basedpyright: 0 errors, 0 warnings
- CLI `--help`: exit 0
- missing source: exit 1, output 미생성
- actual happy/replay: exit 0/0, artifact yes/no

이 context는 actual option data를 연구 feature까지 연결하지만 성과 trial이나 독립
Reviewer를 아직 만들지 않는다. actual skew는 SIP entitlement가
`blocked/insufficient_subscription`이므로 계속 차단하며 spot 숫자로 우회하지 않는다.

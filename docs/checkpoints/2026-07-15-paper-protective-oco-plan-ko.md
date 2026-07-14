# Alpaca Paper 부분체결 보호 OCO 계획 체크포인트

확인일: 2026-07-15

상태: **보호주문 계획·신규 진입 차단 완료, OCO 제출은 미구현**

## 공식 계약에서 확인한 위험

Alpaca native bracket 주문의 take-profit·stop-loss leg는 entry가 완전히 체결된 뒤에만 활성화된다. 따라서 부분체결된 entry를 bracket만으로 즉시 보호할 수 없다. 이미 열린 포지션에는 별도 OCO를 제출할 수 있고, OCO의 take-profit이 부분체결되면 stop-loss 수량이 잔여 수량으로 조정된다. 극단적으로 빠른 시장에서는 cancel 전 두 leg가 모두 체결될 가능성도 공식 문서가 경고한다.

근거: [Alpaca Orders의 Bracket/OCO 계약](https://docs.alpaca.markets/us/docs/orders-at-alpaca)

## 이번 구현

- WSS·Account Activities·REST로 execution 상세가 완전하고 현재 broker 포지션 수량이 누적 entry fill과 정확히 일치할 때만 보호 계획을 만든다.
- long entry는 sell OCO, short entry는 buy OCO로 반전한다.
- 수량은 현재 확인된 정수 포지션 수량과 정확히 같다.
- stop leg는 체결 가능성을 우선해 stop-market 가격을 사용한다.
- 현재 연구 상태기계에서 1R은 중간 관찰 상태이고 2R이 종료 상태이므로 take-profit은 2R limit 하나로 고정한다.
- `time_in_force=day`, `extended_hours=false`로 오버나이트를 허용하지 않는다.
- 보호 client order ID는 parent intent에서 만든 48자 이하 결정론적 값이며 부분체결 수량이 늘어도 동일하다. 향후 새 OCO를 중복 제출하지 않고 replace할 식별 경계다.
- execution 상세 결손, 원장 anomaly, 포지션·symbol·수량 불일치, fractional 수량은 추정하지 않고 차단한다.

현재 broker 보호 OCO를 저장·조회하는 원장이 아직 없으므로 체결 노출이 하나라도 있으면 모든 신규 entry admission을 `PORTFOLIO_BLOCKED`로 바꾼다. 이는 열린 포지션 관리나 stream ingestion을 중단하는 것이 아니라, 보호가 확인되기 전에 계좌 위험을 더 늘리지 않는 안전선이다.

## 검증

- 순수 계획기: partial long 20주 → sell 20주, stop 99, take-profit 102, DAY OCO
- fill 20→35주 증가 시 client order ID 유지와 수량 변경 확인
- execution 상세 불완전 시 계획 거부
- 기존 partial fill과 REST terminal fill이 있어도 보호 OCO 미확인 상태에서는 신규 진입 차단
- 관련 회귀 21개 통과
- 전체 회귀 488개 통과
- Ruff lint·변경 파일 format·basedpyright·no-excuse 통과
- 변경 소스 3개 모두 250 pure LOC 이하
- 수동 library driver에서 `ProtectiveOcoExitPlan sell 20 99 102 day oco` 관찰

이 결과는 실제 OCO가 생성됐거나 포지션이 broker에서 보호됐다는 증거가 아니다. POST/PATCH/DELETE는 계속 비활성이다.

## 다음 구현 게이트

1. parent entry와 OCO parent/stop leg 관계를 append-only 원장에 저장
2. `nested=true` 주문 조회로 OCO 두 leg와 수량·가격·상태를 대사
3. 모호한 POST/PATCH/DELETE timeout을 client order ID와 REST로 멱등 복구
4. kill switch·신규 진입 cutoff·EOD cancel/flatten과 같은 단일 Writer 상태기계에 결합
5. 모두 fake provider와 실제 Alpaca Paper 최소수량 smoke에서 검증한 뒤에만 mutation 공개

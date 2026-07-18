# US Market Data Runtime Supervisor 체크포인트

- 날짜: 2026-07-18
- 마일스톤: M4.3 Read-Only Runtime Supervisor
- provider/network 호출: 0건
- credential/account/order 접근: 0건

## 구현

- provider adapter 계약은 `source_id`와 `read_batch(desired, after_sequence)`만 노출한다. scanner나 adapter에는 추천·계좌·주문 권한이 없다.
- supervisor는 M4.2가 확정한 `SubscriptionPolicyDecision.desired`만 adapter에 전달한다. stale·closed·empty policy는 adapter 호출 전 `blocked_subscription_policy`로 닫힌다.
- 별도 mode-600 SQLite는 raw receipt, runtime incident와 checkpoint를 세 append-only table에 보존하며 UPDATE와 DELETE를 trigger로 금지한다.
- 비차단 file lock으로 runtime writer를 하나만 허용한다. 재시작한 supervisor는 마지막 checkpoint sequence를 adapter offset으로 넘기고 같은 epoch의 저장 완료 봉을 이어서 M4.1에 공급한다.
- raw receipt를 sequence 판단보다 먼저 저장한다. 같은 epoch·sequence의 exact duplicate는 idempotent no-op이고 payload 또는 정규화 봉이 다른 duplicate는 sanitized error로 닫힌다.
- 예상 sequence가 빠지면 `sequence_gap` incident를 남기고 해당 connection epoch 전체의 feature publication을 차단한다. 새 epoch 수신 때 `reconnect` incident를 남기고 sequence 1부터 다시 검증한다.
- gap이 없는 저장 완료 1분봉만 M4.1 kernel로 전달한다. snapshot은 해당 batch의 exact `ResearchInputIdentity`를 유지한다.
- production 모듈 5개는 모두 250 pure LOC 이하이며 provider SDK, network, credential, account, position 또는 order 모듈을 import하지 않는다.

## Fixture E2E

수동 library driver에서 첫 20개 봉은 `blocked_insufficient_history`, 재시작 offset은 `20`, 누적 35개 봉은 `ready`였다. sequence 35 exact duplicate는 `no_new_data`, sequence 36 누락은 `blocked_sequence_gap`, 새 epoch의 1~35 수신은 다시 `ready`였다. 저장 incident 순서는 `sequence_gap`, `reconnect`로 확인했다.

## 검증

- M4.3 focused: **8 passed**
- full repository: **2151 passed**
- Ruff: 통과
- basedpyright: 0 errors/warnings
- compileall: 통과
- no-excuse: 변경 production module 5개 위반 0건

## 다음 단계

M4.4에서 clean runtime feature evidence를 기존 US `OpportunitySnapshot`과 conditional `TradeSignalEnvelope`에 reference로 연결한다. 실제 US provider adapter와 정규장 read-only smoke는 별도 운영 단계이며, fixture 완료를 실시간 coverage나 수익성 증거로 해석하지 않는다.

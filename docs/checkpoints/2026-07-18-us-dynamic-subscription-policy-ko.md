# US Dynamic Subscription Policy 체크포인트

- 날짜: 2026-07-18
- 마일스톤: M4.2 Candidate and Dynamic Subscription Policy
- provider/network 호출: 0건
- credential/account/order 접근: 0건

## 구현

- `BroadScannerSnapshot`은 exact `ResearchInputIdentity`, 관측시각과 immutable 후보만 보존한다. 후보는 instrument, symbol, priority score와 source rank만 제안하며 구독 action 권한이 없다.
- 순수 정책은 quote와 trade 채널을 함께 가진 bounded desired set을 만들고 `us_dynamic_quote_trade_v1` semantic version과 전체 config를 decision에 보존한다.
- ranking은 priority score 내림차순, source rank 오름차순, instrument ID와 symbol 오름차순으로 고정한다. 입력 순서가 달라도 결과가 같다.
- hard capacity가 minimum residency보다 우선한다. 용량 안에서는 아직 residency가 끝나지 않은 incumbent를 보호하고, 나머지는 동일 ranking으로 결정적으로 선택한다.
- 퇴출은 subscribe보다 먼저 실행하도록 action을 정렬하고, 정규장 중 정상 퇴출에만 exact eligible-after cooldown을 생성한다.
- stale snapshot 또는 NYSE 정규장 밖 입력은 desired set을 비우고 active subscription의 unsubscribe action만 만든다. 이 fail-closed 종료에는 새 cooldown을 만들지 않는다.
- duplicate instrument/symbol, scanner-active-cooldown symbol 불일치, active/cooldown 중첩, naive/future time, invalid duration·capacity·score·rank를 sanitized error로 거부한다.
- 모델, 검증과 정책 계산을 세 모듈로 분리해 각 production module을 no-excuse 250 pure LOC 아래로 유지한다.

## 검증

- M4.2 focused: **14 passed**
- M4.0 identity + M4.1 feature kernel + M4.2 policy: **54 passed**
- full repository: **2143 passed**
- Ruff: 통과
- basedpyright: 0 errors/warnings
- compileall: 통과
- no-excuse: 변경 production module 3개 위반 0건

## 다음 단계

M4.3에서 이 policy decision만 실행하는 provider-neutral read-only runtime supervisor를 추가한다. reconnect, duplicate receipt, sequence gap과 restart offset을 append-only evidence로 남기며 provider adapter에는 전략·추천·주문 권한을 주지 않는다.

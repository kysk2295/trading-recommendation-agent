# Alpaca SIP dynamic reconnect policy 체크포인트

## 완료 계약

- terminal store는 exact plan ID의 terminal만 UTC time과 epoch 순서로 읽는다.
- 각 history item은 binding, raw receipt와 terminal content hash를 다시 검증한다.
- `max_attempts`는 1~10의 명시적 bounded budget이다.
- failed terminal 수만큼 budget을 차감하고 남으면 exact next attempt number를 반환한다.
- complete terminal이 하나라도 있으면 새 연결을 차단한다.
- failed 수가 budget에 닿으면 새 연결을 차단한다.
- unordered, duplicate epoch, mixed-plan, multiple complete와 complete 뒤 terminal은 fail-closed한다.

## 검증

- focused 6 passed, full 2433 passed
- Ruff, basedpyright 0/0, compile과 no-excuse rules 통과
- empty, two-failure restart, exhausted, complete, malformed history fixture 통과
- connector·provider·credential·account/order 요청 0건

## 남은 경계

- READY decision을 한 번의 owner invocation에 연결하는 fixture supervisor
- retryable/non-retryable failure 분류와 backoff
- reconnect epoch gap/duplicate provider message soak
- 열린 NYSE 정규장 bounded read-only smoke

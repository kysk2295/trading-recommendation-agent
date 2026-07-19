# Alpaca SIP dynamic reconnect backoff 체크포인트

## 완료 계약

- 첫 failed terminal 뒤 1초, 두 번째 뒤 2초로 증가하고 4초에서 제한되는 deterministic backoff를 추가했다.
- 다음 eligible time은 process-local sleep 횟수가 아니라 verified terminal 시각과 누적 failed attempt 수로 계산한다.
- 재시작은 persisted terminal 이후 이미 지난 시간을 빼고 남은 시간만 기다린다.
- wait는 `Event.wait()` 계약으로 중단 가능하며 stop이면 다음 connector 전에 `STOPPED`를 반환한다.
- 현재 clock이 마지막 terminal보다 과거면 기다리거나 연결하지 않고 `BLOCKED_CLOCK_REGRESSION`으로 닫는다.
- complete, exhausted budget, non-retryable failure와 local receipt integrity의 기존 fail-closed 계약을 유지한다.

## 검증

- focused backoff/supervisor 13 passed, full 2446 passed
- Ruff, basedpyright 0/0, compileall과 no-excuse rules 통과
- timeout 후 backoff 성공, restart 잔여 0.501초, wait 중 stop, clock regression connector 0건 fixture 통과
- local library E2E에서 attempted 2, completed 2, wait 1회, connector 2회를 확인했다.
- fixture connector 외 provider·credential file·account/order 요청 0건

## 남은 경계

- reconnect epoch gap과 duplicate provider message soak는 `2026-07-19-alpaca-sip-dynamic-trade-history-ko.md`에서 완료
- original/correction/cancel active-state canonicalization
- 열린 NYSE 정규장 bounded read-only smoke

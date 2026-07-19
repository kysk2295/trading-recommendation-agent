# Alpaca SIP dynamic reconnect supervisor 체크포인트

## 완료 계약

- supervisor는 owner 호출 전후마다 verified terminal history와 reconnect decision을 다시 읽는다.
- timeout, socket close와 handshake failure만 remaining attempt budget 안에서 재시도한다.
- endpoint, protocol과 subscription ACK failure는 failed terminal 한 건을 남기고 다음 connector 전에 차단한다.
- 기존 `BOUNDED_COMPLETE` history는 connector 0건으로 `BLOCKED_COMPLETE`를 반환한다.
- exhausted failed history는 connector 0건으로 `BLOCKED_BUDGET`을 반환한다.
- restart는 기존 failed terminal을 completed attempt에 포함한다.
- local receipt/schema/hash 오류는 supervisor report로 변환하지 않고 fail-closed 전파한다.

## 검증

- focused supervisor 4 passed, related 16 passed, full 2437 passed
- Ruff, basedpyright 0/0, compile과 no-excuse rules 통과
- timeout 후 success, restart exhaustion, complete short-circuit, invalid ACK fixture 통과
- fixture connector 외 provider·credential file·account/order 요청 0건

## 남은 경계

- retry attempt 사이 bounded interruptible backoff
- reconnect epoch gap과 duplicate provider message soak는 `2026-07-19-alpaca-sip-dynamic-trade-history-ko.md`에서 완료
- original/correction/cancel active-state canonicalization
- 열린 NYSE 정규장 bounded read-only smoke

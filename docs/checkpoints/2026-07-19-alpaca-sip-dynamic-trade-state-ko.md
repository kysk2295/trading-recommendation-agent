# Alpaca SIP dynamic trade active-state 체크포인트

## 완료 계약

- private raw store와 control/ACK를 다시 검증한 dynamic projection만 active-state 입력으로 허용한다.
- original trade ID를 root로 고정하고 correction의 original/corrected ID를 같은 active trade의 alias로 유지한다.
- correction의 exchange, tape, 원가격, 원수량과 원조건이 현재 active 값과 정확히 일치할 때만 새 immutable 상태를 만든다.
- cancel/error는 original 또는 corrected alias 어느 쪽을 가리켜도 root active trade를 제거한다.
- missing target, tombstone 뒤 correction, 이미 사용한 trade ID의 conflicting 재등장과 receipt-time regression은 fail-closed한다.
- exact 동일 payload 재수신은 duplicate count만 증가시키고 active state를 바꾸지 않는다.
- 전체 future chain의 무결성은 검증하지만 `received_at <= as_of`인 receipt만 observed state에 반영한다.
- 같은 raw receipt 안의 메시지는 동일한 인과 시점에 함께 관측한다.

## 검증

- focused active-state 9 passed, dynamic SIP related 61 passed, full 2455 passed
- Ruff, basedpyright 0/0, compileall과 no-excuse rules 통과
- correction alias 이동, original alias cancel, 과거 as-of, invalid chain 4종, clock regression과 quote-only fixture 통과
- local library QA에서 original 101 → correction 102 → cancel 101의 최종 active 0건을 확인했다.
- provider·credential file·account/order 요청 0건

## 남은 경계

- 열린 NYSE 정규장 bounded read-only smoke
- reconnect gap의 provider backfill evidence 계약

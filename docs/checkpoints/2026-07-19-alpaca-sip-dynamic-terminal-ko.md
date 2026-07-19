# Alpaca SIP dynamic terminal 체크포인트

## 완료 계약

- receipt SQLite v1을 기존 binding/receipt row rewrite 없이 v2 terminal schema로 올린다.
- epoch마다 terminal row는 정확히 하나이며 update/delete trigger로 append-only다.
- `BOUNDED_COMPLETE`는 control 3건과 data 1건 이상의 exact receipt IDs를 요구한다.
- `FAILED`는 final URL 전 0건, invalid control/ACK와 timeout 뒤 부분 receipt를 모두 보존한다.
- content hash는 plan ID, epoch, UTC terminal time, status와 ordered receipt IDs를 결합한다.
- terminal 뒤 receipt 추가, row/hash 변조, naive time과 plan mismatch는 replay에서 차단한다.
- read-only v1 DB는 terminal 없음으로 읽고 첫 terminal write에서 v2로 마이그레이션한다.

## 검증

- owner 6, receipt/terminal 13, projection 포함 related 25 passed
- full 2427 passed, Ruff, basedpyright 0/0
- v1 migration, append-only trigger, hash corruption, naive time fixture 통과
- provider·credential file·account/order 요청 0건

## 남은 경계

- failed/complete epoch history에 기반한 bounded reconnect budget
- reconnect 후 sequence gap과 duplicate provider message 처리
- correction/cancel active-state canonicalization
- 열린 NYSE 정규장 bounded read-only smoke

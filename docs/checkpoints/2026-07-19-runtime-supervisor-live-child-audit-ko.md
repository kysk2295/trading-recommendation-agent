# Runtime supervisor live child audit 체크포인트

## 완료 범위

- runtime cycle은 기존 정수 exit facade와 별도로 frozen `exit_code + live_outcome`을 내부 supervisor 계약에 반환한다.
- live outcome은 `disabled`, `not_attempted`, `completed`, `blocked` 네 상태와 selected/new/replay aggregate만 가진다.
- supervisor의 기존 parent attempt canonical payload, attempt ID와 schema v1 table은 변경하지 않는다.
- schema v2의 append-only child table은 parent attempt ID와 1:1이며 parent와 같은 transaction에서만 운영 append된다.

## Migration·재생

- schema v1 query는 읽기만 하고 user version이나 table을 바꾸지 않는다.
- 다음 Writer가 child table과 UPDATE/DELETE trigger를 추가한 뒤 user version 2를 확정한다.
- migration 전후 기존 parent payload bytes가 같은지 검증한다.
- child reader는 parent history를 먼저 완전 재생하고 child payload SHA, content identity, aggregate count, parent binding과 parent 순서를 다시 검증한다.

## 검증

- armed supervisor fixture: parent READY 1, child completed `selected/new/replay=1/1/0`
- blocked operation fixture: parent blocked 1, child blocked 1
- v1 fixture: query-only records 유지, live records 0, user version 1 유지
- v1 next Writer: 기존 parent bytes 유지, schema v2 child append 성공
- child UPDATE trigger와 payload tamper: query-only replay block
- 전체 `2553 passed`
- Ruff, basedpyright `0 errors, 0 warnings`, compileall, changed-file no-excuse 통과

## 안전 경계

- child payload에는 symbol, instrument, 가격, credential, path, account, position과 order ID가 없다.
- 이 변경은 audit schema와 local orchestration 계약이며 실제 Alpaca provider WebSocket과 broker mutation은 0건이다.
- 열린 NYSE 정규장·private market-data credential·SIP entitlement가 모두 맞을 때만 별도 explicit arm smoke를 수행한다.

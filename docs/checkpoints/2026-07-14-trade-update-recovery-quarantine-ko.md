# Alpaca Paper `trade_updates` 복구·격리 체크포인트

## 판정

주문 스트림 frame을 해석하기 전에 원문을 확정하고, 중단 뒤 재시작과 WSS 재연결 때 REST 주문 상태로 원장을 보수적으로 복구하는 기반을 구현했다. 정상 event, protocol quarantine, immutable conflict, REST aggregate와 개별 execution 증거를 구분한다. 이 단계는 실제 주문 생성·교체·취소·청산 기능을 추가하지 않았으며, 외부 동작은 Alpaca Paper WSS와 REST GET뿐이다.

## 구현된 계약

- 실행 원장을 v3로 확장해 raw receipt, disposition, recovery snapshot, recovery order를 append-only 저장한다.
- text와 binary frame의 원문 BLOB, wire kind, connection epoch, 수신시각, SHA-256과 파생 key를 보존한다.
- raw commit 뒤 프로세스가 중단돼도 다음 시작에서 미분류 receipt를 원래 SQLite 행 순서와 메타데이터로 다시 처리한다.
- JSON·event·account·intent·주문 계약 위반은 원문을 잃지 않고 quarantine하며 정상 broker event로 승격하지 않는다.
- 계좌·open 주문·미해결 intent별 주문·최근 7일 주문·포지션을 두 heartbeat 사이에 GET하고 snapshot 하나로 확정한다.
- open 주문 500건 경계와 최근 주문 20×500건 경계를 넘거나 페이지 진행을 보장할 수 없으면 성공으로 축약하지 않고 차단한다.
- 전용 Paper 계정에서 로컬 intent에 없는 open·targeted·recent 주문을 발견하면 차단한다.
- REST 누적 체결량은 누락된 상태를 복원할 수 있지만, 개별 execution ID·수량·가격을 합성하지 않는다.
- 실행 수량·가중평균 가격이 REST aggregate와 다르면 상세 불완전 상태를 이후 복구에도 유지한다.
- terminal 주문의 상태·누적 체결량·평균가격 변경, quantity 회귀, replacement, 불가능한 event/status 조합을 이상으로 기록한다.
- 일반 quarantine은 일관된 후속 REST 복구로 해소할 수 있지만 immutable conflict는 자동 해소하지 않는다.
- 모든 정상 read/write에서 v3 table·trigger·unique index 정의를 검사하고 저장 raw/recovery hash와 파생 key를 다시 계산한다.
- 두 번째 Writer는 WSS 연결 전에 실패하며, frame 수신 뒤 clock·DB·분류·복구 오류가 발생하면 ingestion을 닫고 fail-stop한다.
- 0-byte text/binary frame도 원문 receipt로 보존한 뒤 parser quarantine한다.

## 실제 Paper 계정 읽기 전용 QA

2026-07-14에 기존 v1 production 원장을 v3로 migration하고 다음을 실제 Alpaca Paper 계정에 실행했다.

1. `bootstrap`: 기존 계좌 binding 확인, 계좌·주문·포지션 GET 성공
2. `recovery`: WSS 인증·`trade_updates` 구독, heartbeat로 둘러싼 REST recovery snapshot 저장 성공
3. `readiness`: WSS, REST, 원장, 포트폴리오 대사 성공

당시 계정에는 open/recent 주문과 포지션이 없어 recovery order 0건, raw receipt 0건이었다. 실제 주문 intent를 만들지 않았고 POST/PATCH/DELETE를 호출하지 않았으므로 신규 주문 admission과 체결 처리의 장중 성과는 미평가다. 이 성공 결과는 종료된 연결의 주문 승인으로 재사용하지 않는다.

## 검증 기준

- raw-first commit, 중단 뒤 pending 분류, quarantine, immutable conflict, REST 페이지 경계와 미지 주문 차단을 회귀 테스트한다.
- terminal lifecycle, aggregate/execution 수량·가격 불일치, hash·스키마 손상, invalid UTF-8 secret과 CLI 오류 경계를 회귀 테스트한다.
- 전체 pytest, Ruff, basedpyright, lockfile, diff, CLI help·실패·실계정 GET-only 경로를 모두 통과한 뒤 병합한다.

## 의도적으로 남긴 차단 경계

- Alpaca Trading WebSocket은 replay cursor, exactly-once, 애플리케이션 event high-water를 보장하지 않는다.
- recovery와 readiness가 별도 WSS 연결을 열 수 있으므로 현재 snapshot과 주문 admission 사이에 원장이 변하지 않았음을 아직 하나의 경계로 증명하지 못한다.
- 하나의 장수명 stream owner가 ingestion·current-epoch recovery·admission을 직렬화하고 동일 ledger generation을 확인하기 전에는 주문 POST를 열지 않는다.
- Account Activities의 fill·trade correction·trade cancel/bust를 `since_id`로 복구하는 경로가 필요하다.
- 부분체결 즉시 보호 손절·목표, cancel race, 일일 kill switch, 신규진입 중단, 마감 전 강제청산이 남아 있다.
- 실제 정규장 Paper 거래 60일·100건과 broker/shadow 양쪽의 승격 기준은 아직 시작하지 않았다.

## 다음 체크포인트

단일 장수명 WSS 소유권과 원장 generation barrier를 먼저 구현한다. 그다음 Account Activities 기반 체결 정정 복구와 보호주문·kill/EOD 상태기계를 같은 Writer 안에 연결한다. 실제 주문 기능은 이 안전 경계가 검증된 뒤에도 Alpaca Paper에서만 제한적으로 연다.

## 공식 근거

- [Alpaca Trading WebSocket](https://docs.alpaca.markets/us/docs/websocket-streaming)
- [Alpaca Orders lifecycle](https://docs.alpaca.markets/us/docs/orders-at-alpaca)
- [Alpaca Working with orders](https://docs.alpaca.markets/us/docs/working-with-orders)
- [Alpaca Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading)

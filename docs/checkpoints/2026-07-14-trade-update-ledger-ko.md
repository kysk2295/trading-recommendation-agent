# Alpaca Paper `trade_updates` 원장 체크포인트

## 판정

Alpaca Paper 주문 스트림의 JSON binary frame을 타입 검증한 뒤, 계좌에 결합된 단일 Writer 원장에 중복 없이 저장하고 재시작 후 부분체결 수량을 복원하는 기반을 구현했다. 이 단계는 주문 생성·교체·취소를 추가하지 않았으며 실제 자금 및 live endpoint를 사용하지 않았다.

## 구현된 안전 계약

- 공식 문서의 17개 주문 이벤트를 명시적으로 파싱한다.
- 미국 주식, 알려진 local intent, 동일 symbol·side·quantity·limit·TIF·extended-hours 조건만 정상 원장에 기록한다.
- 원장과 다른 Paper 계좌, 미결합 계좌, 미지 client order ID, 연결되지 않은 두 번째 broker order ID는 저장 전에 차단한다.
- 실제 ingestion capability는 같은 credentials로 WebSocket과 REST 계좌를 열고, REST가 확인한 account fingerprint와 WebSocket이 생성한 connection epoch를 호출자 입력이 아닌 세션 내부 값으로 고정한다.
- fill·partial fill은 `execution_id`, 그 밖의 이벤트는 `event_id`, 둘 다 없으면 나노초 원문을 보존한 canonical payload SHA-256 순으로 이벤트 키를 만든다.
- 재연결 세대와 수신시각은 최초 receipt 메타데이터로만 보존하고 중복키에는 넣지 않는다.
- 부분체결 개별 수량과 누적 수량이 맞지 않으면 누락 가능성으로 표시하고 신규 진입 readiness를 차단한다.
- 마지막 execution이 누락되어 cancel/reject 스냅샷의 누적 체결량만 늘어난 경우도 최종 execution 합과 비교해 이상으로 표시한다.
- partial fill 뒤 cancel된 주문도 실제 포지션 수량과 결합할 수 있다.
- 원장에 체결 증거가 있는데 REST 포지션 또는 활성 부분체결 주문이 없으면 torn snapshot으로 간주해 포트폴리오 승인을 차단한다.
- fill 뒤 늦게 도착한 accepted 등 비종료 이벤트가 intent를 다시 열지 않는다.
- `fill+canceled`처럼 상호 배타적인 종료 이력과 event/order status 모순은 정상 상태가 될 수 없다.
- replacement chain은 보존하지만 현재 pilot에서는 이상 상태로 간주해 REST 재대사 전 신규 진입을 막는다.
- 완전한 주문 snapshot이 없는 legacy broker lifecycle event는 계좌 binding을 통과해도 authoritative terminal로 인정하지 않고 REST 재대사를 요구한다.
- v1 원장은 행을 보존한 채 한 트랜잭션으로 v2에 올리고, 손상된 migration은 `user_version=1`로 롤백한다.
- 새 trade update 표와 기존 intent·event 표는 UPDATE·DELETE를 DB trigger로 거부하며, v2를 열 때 객체 이름뿐 아니라 table·trigger·unique index 정의 전체가 기준 스키마와 같은지 검사한다.
- GET-only preflight는 intent·event·projection·account binding을 한 read transaction으로 읽어 execution gap과 legacy anomaly를 누락하지 않는다.

## 검증 범위

- binary frame 수신부터 Pydantic 경계, SQLite 저장, 프로세스 재시작, 다른 connection epoch의 replay까지 라이브러리 표면으로 수동 실행했다.
- production ingestion composition과 같은 표면에서 REST account binding, stream epoch 보존, 동일 frame replay 1행 보존, 다른 계좌 사전 차단을 테스트했다.
- 정상 partial fill은 1행만 남고 누적 체결량 10주, anomaly 없음으로 복원됐다.
- symbol이 intent와 다른 입력은 `TradeUpdateOrderMismatchError`로 차단됐다.
- 전체 테스트, Ruff, basedpyright, lockfile, diff 검사와 변경 파일 no-excuse 규칙 검사를 통과해야 이 체크포인트를 병합한다.

## 남은 경계

- Trading WebSocket은 replay·cursor·순서·exactly-once를 보장하지 않는다. 매 연결·재연결 세대에서 스트림을 연 뒤 REST orders·positions와 원장을 다시 대사해야 한다.
- 문서 밖의 새 이벤트나 알려진 이벤트의 필드 결손을 raw quarantine에 남기는 경로는 다음 체크포인트에서 추가한다. 현재는 protocol error로 fail-closed한다.
- ingestion capability는 연결됐지만 장시간 실행·자동 reconnect·재연결 직후 REST recovery를 담당하는 scheduler loop는 다음 체크포인트다.
- 실제 정규장 Paper 주문에서 생성된 trade update는 아직 관찰하지 않았다. 이번 증거는 synthetic frame과 실제 Paper 읽기 전용 연결 증거를 분리해 해석해야 한다.
- 부분체결 직후 보호 손절·목표 주문, cancel race, kill switch, EOD flatten이 완성되기 전에는 POST·DELETE 경로를 공개하지 않는다.

## 공식 근거

- [Alpaca Trading WebSocket](https://docs.alpaca.markets/us/docs/websocket-streaming)
- [Alpaca Orders lifecycle](https://docs.alpaca.markets/us/docs/orders-at-alpaca)
- [Alpaca Working with orders](https://docs.alpaca.markets/us/docs/working-with-orders)
- [alpaca-py TradingStream](https://github.com/alpacahq/alpaca-py/blob/v0.43.5/alpaca/trading/stream.py)

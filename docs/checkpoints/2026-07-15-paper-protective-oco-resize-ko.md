# Alpaca Paper 보호 OCO staged cancel·replacement 체크포인트

날짜: 2026-07-15

상태: **부분체결 증가 보호 OCO 상태기계 완료, 실제 Alpaca Paper POST/DELETE 0건 유지**

## 범위

이번 단계는 `intraday_momentum`의 A단계 실행 안전 경계를 완성하기 위한 것이다. 승인된 lane 아키텍처를 바꾸거나 lane control-plane을 앞당기지 않았으며, 기존 단일 Writer/current-epoch 운영 세션 주위에 점진적으로 추가했다.

- 기존 보호 OCO가 현재 broker 포지션을 정확히 덮으면 mutation 없이 noop한다.
- 누적 entry 체결이 기존 OCO 수량보다 커지면 한 호출에서 기존 OCO DELETE만 실행한다.
- cancel이 terminal로 대사되기 전에는 replacement 계획을 저장하거나 POST하지 않는다.
- 다음 호출의 새 current-epoch에서 exact 포지션과 보호 leg 체결을 다시 확인한 뒤에만 predecessor plan key를 포함한 새 deterministic client ID로 replacement OCO를 제출한다.
- 한 leg 부분체결 뒤 broker가 반대 leg 잔량을 현재 포지션과 같게 조정한 경우는 정확한 보호로 인정한다.
- 양 leg 체결, 중복 OCO, 설명되지 않는 포지션, `pending_cancel`, 유효하지 않은 수량은 자동 복구하지 않고 fail-closed한다.

## Mutation 원장

- 실행 원장을 schema v9로 올리고 `cancel_protective_oco` operation을 추가했다.
- v8 intent/event 행은 migration 중 값이 바뀌지 않는 것을 회귀로 확인했다.
- DELETE intent는 immutable 보호 계획 key와 recovery에서 이미 관측된 exact parent broker order ID에 결합한다.
- open·terminal OCO 이력은 leg 종류·주문 타입·DAY TIF·extended-hours 금지·가격 nullability·finite 수량까지 immutable 계획과 일치해야 한다.
- DELETE attempt를 broker 호출 전에 append하고 ACK·거절·모호 상태에서는 재전송하지 않는다.
- timeout 뒤에는 exact broker order ID targeted GET으로만 terminal 효과를 복구한다.

## 현재시점 게이트

보호 OCO POST/DELETE는 신규 진입용 1분봉을 요구하지 않지만 다음 조건을 모두 요구한다.

- REST account·market clock·nested OCO와 WSS heartbeat가 평가시각 기준 현재 5초 이내
- ACTIVE Paper 계좌, trading block 없음, 원장 account fingerprint 대사 완료
- Alpaca `is_open`과 로컬 NYSE 정규장 및 당일 폐장시각 일치
- EOD 평탄화 시작인 폐장 5분 전보다 이른 시각
- mutation 전후 동일 stream epoch와 원장 generation 경계

## CLI 계약

`run_alpaca_paper_protective_oco_smoke.py`는 cancel 단계가 ACK여도 replacement가 남았으므로 `incomplete`와 종료코드 2를 반환한다. 보고서에는 broker order ID, request ID, mutation key, source plan key, account fingerprint, 자격증명 또는 raw payload를 쓰지 않는다. exact coverage나 남은 포지션 없음은 noop/0, 자체 current-epoch 대사까지 끝난 OCO ACK만 0이다.

## 검증

- 전체 회귀: `628 passed`
- staged 보호 lifecycle·mutation·operating·CLI·immutable recovery 표적 회귀: `58 passed`
- `uv run ruff check .`: 통과
- `uv run basedpyright`: `0 errors, 0 warnings`
- `./run_alpaca_paper_protective_oco_smoke.py --help`: 종료코드 0
- 잘못된 `--arm-paper-mutation`: argparse 종료코드 2, session 미개방
- 임시 SQLite + fake session cancel 단계: 종료코드 2, `incomplete`, 내부 식별자 redaction 확인
- 같은 fake session replacement ACK: 종료코드 0

수동 QA 시점은 2026-07-14 19:40 EDT로 정규장이 닫혀 있었다. 실제 Alpaca 자격증명이나 mutation adapter를 열지 않았고 이번 체크포인트의 외부 Paper POST/DELETE는 0건이다. fake broker 결과는 주문 수명주기 안전성 검증이지 실제 체결 품질이나 전략 수익성의 증거가 아니다.

## 다음 안전 게이트

1. 다음 열린 정규장에서 기존 축소 한도를 그대로 유지해 entry 1건, 즉시 보호 OCO, WSS·REST·Account Activities·원장 대사를 수행한다.
2. 추가 부분체결이 자연스럽게 발생한 경우에만 staged cancel·replacement를 검증하며 체결을 억지로 만들지 않는다.
3. armed safety cancel/flatten 뒤 open order 0, position 0, broker/shadow/원장 일치를 최종 확인한다.
4. 이 A/B 체크포인트 뒤에만 `LaneId`부터 lane control-plane 계약을 추가하고, 그 다음 ORB daily forward-validation loop를 연결한다.

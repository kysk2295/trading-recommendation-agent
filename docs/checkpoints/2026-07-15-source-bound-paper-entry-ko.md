# Source-bound Paper entry 체크포인트

날짜: 2026-07-15

상태: **free-form entry 입력 제거와 current ORB source 결합 완료, 실제 Alpaca Paper POST/DELETE 0건**

## 변경된 production 경계

`run_alpaca_paper_entry_smoke.py`는 더 이상 intent ID, 종목, 가격, 시각, spread 또는 liquidity 수량을 CLI에서 받지 않는다. 허용되는 입력은 exact arm, execution DB, output directory와 watch DB뿐이다.

`load_current_orb_paper_entry()`는 watch SQLite를 `mode=ro`와 `PRAGMA query_only = ON`으로 열고 한 read transaction에서 다음 source를 결합한다.

- `recommendations`의 `opening_range_breakout`·`setup` 행
- 같은 instant에 저장된 `candidate_input_snapshots` 행
- 같은 exchange·symbol·완료 시각의 `candidate_minute_bars` 행

현재 시각 기준 직전 완료 정규장 1분봉, 완료 뒤 최초 관찰, 30초 이내 생성, canonical recommendation ID, 대문자 symbol/exchange, 양수 volume, 유한한 non-negative spread와 `0 < stop < entry < target1 < target2`가 모두 맞는 요청이 정확히 하나일 때만 통과한다. recommendation ID는 intent/client order ID 계보로 유지하고 liquidity 허용량은 1주, 위험 설정은 기존 intraday pilot contract로 고정한다.

source loading은 Paper credential loading과 운영 세션 개방보다 먼저 실행된다. source가 통과해도 current-epoch 운영 세션이 broker clock, NYSE 정규장, 직전 완료 봉, WSS heartbeat, 계좌·원장·broker/shadow 포트폴리오와 위험 한도를 다시 검증한다.

## 안전 불변식

- Alpaca trading base URL은 `https://paper-api.alpaca.markets` 고정
- live endpoint와 실계좌 주문 경로 없음
- 최대 notional 100 USD, 계획위험 10 USD, 최대 1포지션, 일손실 30 USD, 편도 비용 20bp 유지
- source 0건·중복·stale·불완전·손상은 generic source error로 fail-closed
- source error는 credential·WSS·REST 이전에 발생하며 민감한 DB path나 원문 오류를 출력하지 않음
- 잘못된 arm은 argparse에서 execution/watch DB 생성과 네트워크보다 먼저 차단
- 기존 OCO, 부분체결, timeout, 재시작 복구, EOD safety 상태기계와 lane 계약은 변경하지 않음

## 검증 근거

- exact source loader: `14 passed`
- source loader + entry CLI focused 회귀: `21 passed`
- 전체 회귀: `801 passed`
- `uv run ruff check .`: 통과
- 변경 Python 4개 `ruff format --check`: 통과
- `uv run basedpyright`: 오류 0, 경고 0
- `git diff --check`: 통과

직접 CLI QA:

- `./run_alpaca_paper_entry_smoke.py --help`: 종료코드 0, arm·execution DB·output dir·watch DB만 표시
- 잘못된 arm `WRONG`: 종료코드 2
- 잘못된 arm QA 뒤 지정한 execution DB, watch DB와 output dir: 모두 생성되지 않음
- Paper credential 파일: 값 조회 없이 존재 여부만 확인했으며 현재 부재
- repository `outputs/`: 현재 부재
- 외부 Alpaca Paper POST/DELETE: 0건

## 커밋

- `ace296d feat: bind Paper entry to current ORB source`
- `74dde40 feat: require source-bound Paper entry CLI`

## 다음 검증

첫 정규장 smoke는 fixed Paper credential과 현재 ORB source가 실제로 존재하는 열린 정규장에서만 실행한다. 순서는 entry 1건, 체결 대사, 즉시 보호 OCO, timeout/재시작 recovery, staged cancel/flatten, open order 0·position 0 최종 broker/shadow/원장 대사다. 조건이 부족하면 실제 mutation을 만들지 않는다.

이 체크포인트는 기능·안전 계약 검증이며 수익성, champion 승격 또는 위험 확대 근거가 아니다.

# US Day Paper 현재 세션 준비상태 체크포인트

기준 시각: 2026-07-22 07:02 EDT  
코드 기준: `d6810f6e7502d75f7d829811c3902fbd341229d0`

## 실제 확인 결과

- Alpaca trading endpoint는 `paper-api.alpaca.markets`로 고정되어 있다.
- 2026-07-22T10:58:35Z readiness에서 주문 스트림 인증, 구독, Pong과
  활성 스트림 내부 REST, 원장, 포트폴리오 대사가 통과했다.
- `run_us_day_operating_session.py recover`를 실제 Paper 계정에 실행한 결과
  미체결 주문 0건, 열린 포지션 0건, 대사 사유 없음으로 복구가 통과했다.
- 실행 원장의 `paper_mutation_events`, `paper_mutation_intents`,
  `order_intents`, `broker_order_events`는 모두 0건이다.
- Paper 자격증명 파일과 실행 원장은 현재 사용자 소유의 mode `600` 파일이다.
- 주문 POST/DELETE는 실행하지 않았다.

## 현재 차단 상태

- 확인 시점은 뉴욕 정규장 전이며 broker market은 닫혀 있다.
- 오늘 ORB 감시는 정상 동작 중이지만 정규장 current-bar 추천 원장인
  `outputs/live_sessions/20260722/paper_recommendations.sqlite3`는 아직 생성되지 않았다.
- 존재하지 않는 오늘 watch 원장으로 `preflight`를 실행하면
  `invalid_current_orb_source`로 차단된다.
- Hermes Paper arm 서명키와 arm 원장은 현재 존재하지 않는다. 따라서 owner가
  명시적으로 arm하지 않은 주문은 실행할 수 없다.
- 이 상태는 M2 실제 Paper lifecycle 완료 증거가 아니다. 실제 Paper POST는 여전히 0건이다.

## 정규장 실행 계약

다음 실제 mutation은 아래 조건이 동시에 충족될 때만 허용한다.

1. 현재 뉴욕 정규장이 열려 있다.
2. 오늘 ORB 감시기가 자연스럽게 만든 유일한 setup이 있다.
3. setup이 현재 세션의 최신 완료 1분봉과 30초 이내 관측값에 결합된다.
4. REST, 주문 스트림, broker/shadow 원장, 계좌 binding 대사가 모두 통과한다.
5. 등록된 Paper champion과 정확히 결합된 owner 서명 일회성 arm이 있다.
6. 기존 위험 한도와 endpoint guard가 변경 없이 통과한다.

조건이 충족되면 기존 `run_us_day_operating_session.py run` 하나가 entry, 보호 OCO,
부분체결·재시작 복구, EOD 평탄화, terminal 대사와 Hermes 결과 전달을 소유한다.
자연 setup이 없으면 임계값을 낮추거나 setup을 인위적으로 만들지 않고
`censored_no_setup` 세션 증거를 남긴다.

## 관찰한 CLI QA

- `run_us_day_operating_session.py --help`: exit 0
- `run_us_day_operating_session.py preflight --help`: exit 0
- `run_us_day_operating_session.py recover --help`: exit 0
- 실제 Paper `recover`: exit 0, `result=recovered`
- 누락된 current-session watch 원장 `preflight`: exit 1,
  `reason=invalid_current_orb_source`


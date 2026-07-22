# US Day session terminal·3-session evidence 체크포인트

## 완료한 코드 경계

- `run_us_day_operating_session.py`는 `preflight`, `run`, `recover`, `finalize`, `evidence` 운영 surface를 제공한다.
- `preflight`와 `recover`는 Alpaca Paper GET/WSS readiness만 읽고 mutation recovery를 호출하지 않는다.
- `run --terminal-output`은 completed뿐 아니라 blocked·incident도 read-only current state와 명시적 이유를 결합해 일일 terminal로 남긴다.
- terminal source는 별도 immutable `--source-artifact`가 필수이며 mutable watch SQLite를 증거 hash로 추정하지 않는다.
- no-setup finalizer는 clean commit, 실제 NYSE 거래일, 장 종료, 주문·포지션·보호 OCO 0, broker/shadow 대사 일치와 source hash를 Hermes outcome 투영 전에 검증한다.
- incident가 장후 복구된 경우 기존 이유를 보존하면서 `flat`·`reconciled` final state를 갱신한다.

## 3-session gate

증거 builder는 다음 private mode-600 파일을 만든다.

- `outputs/acceptance/us_day/three_session_report.json`
- `outputs/acceptance/us_day/natural_paper_lifecycle.json`
- `outputs/acceptance/us_day/final_reconciliation.json`
- `outputs/acceptance/us_day/hermes_outcome_receipt.json`
- `outputs/acceptance/day/manifest.json`

중복 session terminal은 거부하고 실제 예정된 NYSE session만 eligible로 계산한다. 세 session 모두 flat·대사·Hermes receipt를 통과하고, 적어도 한 session이 실제 entry·보호 OCO·flat·대사·outcome 수명주기를 가져야 US operating subgate가 완료된다. censored no-setup 표본은 전달 신뢰성 표본이지만 자연 Paper lifecycle을 대신하지 않는다.

## 안전 상태

이 체크포인트의 개발·QA에서는 Alpaca Paper POST/DELETE와 외부 Telegram 전송을 실행하지 않았다. 실제 정규장 증거는 위 런북의 명시적 owner arm과 기존 고정 위험한도 아래에서만 수집한다. 실자금 endpoint와 KIS·LS 주문 경로는 계속 존재하지 않는다.

## 검증

- 집중 회귀: 30 passed
- 전체 회귀: 3236 passed
- 전체 basedpyright: 0 errors, 0 warnings, 0 notes
- compileall: 통과
- Task 8 변경 파일 Ruff와 no-excuse: 통과
- 전체 Ruff: Task 8 밖의 기존 `trading_agent/us_news_catalyst_day_session_commands.py:267` E501 한 건만 남음
- 실제 CLI: 전체 help, source 누락 차단 exit 1, clean commit 3-session evidence exit 0 확인
- 실제 CLI evidence: 5개 artifact 모두 mode `600`, `operating_product_complete=true` fixture 확인

# KR current calendar와 M3 shadow 배치 체크포인트

## 닫은 운영 결함

KR day trial은 당일 KST 장 전 5분 이내에 관측한 KIS 휴장일 snapshot을 요구하지만, 기존에는 calendar client, projection, private store와 검증만 있고 production에서 이를 실행하는 CLI가 없었다. 이 상태에서는 scheduler가 정상적인 trial을 사전등록할 수 없었다.

`run_kis_kr_session_calendar_collect.py`는 현재 KST 날짜를 내부 시계에서만 결정하고 다음 작업을 직렬 실행한다.

1. 기존 KIS live credential loader와 exact official client를 사용한 휴장일 GET
2. raw response receipt에서 immutable session calendar snapshot projection
3. current-owner mode-600 private SQLite append 또는 exact replay
4. snapshot ID와 신규/재사용 수만 담은 mode-600 redacted report

CLI에는 날짜·시각 override, fixture, 계좌, 주문, arm 또는 임의 endpoint 옵션이 없다. KIS 계약은 기존 exact `GET /uapi/domestic-stock/v1/quotations/chk-holiday`, TR `CTCA0903R`이며 계좌·주문 endpoint를 사용하지 않는다.

## 2026-07-23 production pilot

clean checkpoint `960de7e0571728653966c36dfacfae7d07b44814`를 두 production shadow strategy의 code version으로 사용했다.

- Opportunity: `kr-theme-keyword-projection-v1-code-602c6afe539aa8a8`
- Day: `kr-theme-leader-vwap-reclaim-v1-code-602c6afe539aa8a8`
- operating mode: 둘 다 `shadow`
- Opportunity lane: `kr_equities/opportunity_manager/theme_momentum`
- Day lane: `kr_equities/day_trading/theme_leader_vwap_reclaim`
- order authority: false

첫 production keyword pilot은 `반도체`, `HBM`, `AI 반도체`와 삼성전자·SK하이닉스의 direct-business 관계만 사전등록한다. 이는 현재 rules 품질이나 수익성의 증거가 아니라 same-cycle forward-validation 후보이며, 자동 승격이나 allocation 입력이 아니다.

## 예약 실행

launchd label `ai.trading-agent.kr-m3-20260723`가 PID `42834`로 대기한다. exact detached runtime은 `/private/tmp/trading-agent-kr-m3-20260723-960de7e`이며 dirty main 작업트리를 실행 코드로 사용하지 않는다.

1. 08:55 KST: current-date calendar GET과 snapshot append
2. 08:55 이후: exact composite와 2026-07-23 day trial 사전등록
3. 09:00 KST: exact trial start event
4. 09:05 KST: OpenDART, LS NWS, KIS ranking, local volume surge same-cycle 수집과 Opportunity projection
5. unique Opportunity이 있을 때만 immutable onboarding 후 KIS GET-only minute/current-price/order-book shadow tick
6. 분별 tick, 15:30 EOD collection, post-session terminal, query-only verifier와 open-smoke attestation

현재 Mac에는 mode-600 KIS와 LS 설정은 있지만 OpenDART 설정 파일은 없다. 09:05 전에도 이 상태가 유지되면 source cycle은 incomplete incident를 Hermes delivery outbox에 남기고 Opportunity과 onboarding을 열지 않는다. 이미 시작된 day trial은 즉시 버리지 않고 15:32까지 대기한 뒤 기존 post-session 수직에서 `CENSORED/no_shadow_entry_artifact` terminal, Hermes 무추천, 독립 Reviewer와 next-session lifecycle evidence로 닫는다. source가 완성됐지만 Opportunity이 0건인 경우도 같은 종료 계약을 사용한다.

이 보완은 source incomplete를 성과 0으로 평가하지 않는다. source incident와 censored day terminal은 서로 다른 evidence로 보존되며, Reviewer는 censored session을 data-quality blocker로 처리한다. exact runtime의 no-entry terminal·delivery·Reviewer/lifecycle focused E2E 4개와 post-session `--help` exit 0, 필수 인자 누락 exit 2가 통과했다. 수정된 wrapper는 `zsh -n`과 dry-run 뒤 장 시작 전 sleep 상태에서만 재제출했으며 US와 Hermes 프로세스는 재시작하지 않았다.

wrapper는 exact SHA와 clean runtime, private policy/ledger/delivery file을 먼저 확인한다. `zsh -n`과 `DRY_RUN=1`은 통과했고 dry-run 이벤트에는 `paper_false`, `account_false`, `order_false`가 기록됐다. stdout/stderr는 등록 직후 모두 0바이트였고 wrapper, event log, stdout, stderr는 mode 700/600이다.

## 검증

- TDD RED: calendar CLI 부재와 auth/client/store 경로 부재를 순서대로 확인
- focused calendar/trial: `11 passed`
- full pytest: `3311 passed in 185.98s`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- compileall, diff check, Python no-excuse: 통과
- actual CLI `--help`: exit 0
- actual CLI required argument omission: exit 2
- production calendar GET: 아직 예약 전이므로 0건
- 국내 account/order mutation, Alpaca Paper POST: 0건

Allocation Manager 금지는 유지한다. 최소 두 개의 독립 executable champion이 실제 forward evidence와 Reviewer gate를 통과하기 전에는 이 pilot을 allocation authority로 사용할 수 없다.

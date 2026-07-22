# US 장마감 복구와 M5-M7 병렬 통합 체크포인트

기준일: 2026-07-23 KST
기준 브랜치: `codex/integration-20260723`
기준 base: `d4f30f1bca0bc4f2e4c2fa65d9fd5ba06677fd82`

## 2026-07-22 US 세션 결과

정규장 감시 원장에는 causal ORB 추천이 없었다. scheduled close finalizer는
`no_recommendation` delivery를 만들고 실제 Hermes acknowledgement까지 받았지만,
terminal 파일을 쓰기 전에 재시작되면 같은 immutable delivery identity에 다른
관측 시각을 넣어 충돌했다.

`project_us_day_no_recommendation`은 이제 같은 delivery identity가 이미 있으면
`occurred_at`을 제외한 모든 의미 필드가 정확히 같은 경우에만 기존 이벤트를
재사용한다. 다른 필드가 하나라도 다르면 기존 fail-closed conflict를 유지한다.

실제 동일 production delivery 원장과 Alpaca Paper read-only 상태로 finalizer를
재실행해 다음을 확인했다.

- 결과: `censored_no_setup`
- Hermes acknowledgement: true
- open orders, positions, protective OCO: 모두 0
- broker/shadow reconciliation: 일치
- terminal mode: `0600`
- Paper mutation intents/events, broker order events: 모두 0
- 실제 주문 POST/DELETE와 arm 소비: 0

## 병렬 구현 통합

각 구현은 `origin/main`의 동일 base에서 독립 worktree로 개발·검증됐다.

- M5 Swing historical replay source head: `77520b56aa127f12cad023e908e3144c7720d697`
- M6 intraday automated research/reviewer source head: `110cafd4e60335f485b7f00c32a6df099ad02ad8`
- M7 systematic regime signal-only source head: `79b15fd35b33ea0a626e6cf0b0c2fecd5be6aa83`

통합 결과는 다음 실제 제품 흐름을 추가한다.

1. Swing: causal multi-session replay, recommendation/no-recommendation card,
   shadow entry/exit와 Reviewer evidence
2. Intraday research: bounded walk-forward challenger bundle, 비용·슬리피지 평가,
   query-only Reviewer의 promote/hold/demote 결정
3. Systematic regime: GET-only ETF history, causal regime/breadth replay,
   당일 signal-only card와 recoverable shadow lifecycle

`market_regime`은 계좌·주문·allocation 권한이 없는 signal-only lane으로 유지한다.
세 vertical 모두 수익성·champion 승격 증거가 아니라 forward-validation 후보와
실행 연결 증거다. Allocation Manager gate는 서로 다른 executable champion 두 개가
생기기 전까지 닫혀 있다.

## 통합 전 검증

- US close replay 회귀: red에서 immutable conflict 재현, 수정 뒤 green
- US close 관련 집중 회귀: 14 passed
- M5/M6/M7과 US close 통합 집중 회귀: 67 passed
- 실제 US finalizer manual QA: exit 0, `censored`, acknowledgement true
- 실계좌·live-money endpoint, KIS/LS 주문 경로 사용: 0

전체 pytest, Ruff, basedpyright와 독립 review lane은 이 체크포인트 커밋 뒤 정확한
통합 SHA에서 수행한다.

## 최초 통합 리뷰에서 수정한 결함

최초 통합 SHA 리뷰는 다음 두 문제를 차단했다.

1. M5가 root WATCH/no-recommendation 카드만으로 완료를 판단해, terminal delivery나
   Reviewer append 전에 중단된 replay를 복구하지 못했다.
2. M6 heavy-run lock이 기존 hard link에 `fchmod(0600)`을 적용해 연결된 파일의
   권한을 바꿀 수 있었다.

M5 완료 판단은 이제 요청 날짜의 root 카드뿐 아니라 각 signal의 shadow terminal,
완료 trial event, terminal delivery와 Reviewer evidence를 모두 요구한다. 중간 상태를
복구할 때 이미 관찰된 미래 terminal을 앞선 시각에 finalize하지 않고, persisted
source 시각부터 순서대로 재개한다.

M6 lease는 private parent descriptor, exact mode `0600`, 소유자, regular file,
single-link, name-to-descriptor identity를 lock 전·후에 확인한다. parent/final symlink,
hard link, 비공개 권한 위반과 lock name 교체는 모두 원본을 변경하지 않고 차단한다.

- blocker RED: M5 1건 + M6 4건, 총 5 failed
- 수정 뒤 집중 회귀: 11 passed
- 변경 파일 Ruff: passed
- 변경 파일 basedpyright: 0 errors, 0 warnings, 0 notes

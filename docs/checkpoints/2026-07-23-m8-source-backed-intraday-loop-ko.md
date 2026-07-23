# M8 source-backed intraday research loop 체크포인트

## 제품 결과

전역 experiment ledger의 출처 기반 가설이 다음 경로를 실제로 통과한다.

```text
ResearchSource + ResearchHypothesisCard
→ content-addressed strategy_design queue
→ approved intraday template
→ immutable StrategyVersionRegistration
→ bounded historical replay
→ independent Reviewer artifact
```

새 v2 manifest는 queue snapshot ID, card key, hypothesis ID, strategy mode와 새 strategy
version을 함께 고정한다. 구현기는 임의 코드를 생성하지 않고 기존에 테스트된
VWAP reclaim, HOD breakout, Gap-and-Go 템플릿의 parameter/data/cost/portfolio 계약만
사용한다. ORB는 이 challenger bundle에서 계속 제외한다.

## 불변성과 차단 규칙

- queue artifact 자체 hash와 manifest의 snapshot ID가 정확히 같아야 한다.
- queue item과 ledger card의 가설, 반증 규칙, 경제적 기제, counterfactual, source key/kind를 다시 대조한다.
- `strategy_design` route와 `intraday_momentum` lane만 version 등록을 허용한다.
- manifest 등록시각은 queue 전체 관측시각과 card 등록시각보다 빠를 수 없다.
- 이미 version이 있으면 완전히 같은 registration만 exact replay한다.
- 과거 queue snapshot을 다른 strategy version 생성에 재사용하면 차단한다.
- v2가 queue artifact 없이 실행되면 experiment ledger를 변경하기 전에 차단한다.
- v1 M6 bundle은 기존 bootstrap과 code-coupled version 계약을 그대로 유지한다.

trial은 source card의 exact `ExperimentScope`와 새 strategy version을 사용한다. 결과는
`historical_replay` trial의 `started → completed|failed` chain과 mode-600 artifact로
보존되고, 독립 Reviewer는 `promote|hold|demote` 권고만 만든다. lifecycle, champion,
allocation과 order authority는 변경하지 않는다.

## 수동 CLI QA

committed `us-vwap-reclaim-source-v2.json`과 `intraday-source-backed-v2.json`, 로컬 1-session
CSV fixture로 확인했다.

- CLI help: exit `0`, `--source-queue-artifact` 노출
- v2 queue artifact 누락: exit `1`, blocked
- source/card 등록과 queue projection: exit `0/0`
- bounded loop 첫 실행: exit `0`, trial/review artifact 신규 `1/1`
- exact replay: exit `0`, trial/review artifact 신규 `0/0`
- Reviewer decision: `hold`
- trial/review artifact mode: `600/600`
- external provider, credential, account, broker와 order mutation: `0`

## 검증

- source-backed/queue/ledger/reviewer focused: `90 passed`
- full pytest: `3386 passed`
- Ruff: pass
- basedpyright: `0 errors, 0 warnings, 0 notes`

이 fixture의 `hold`는 수익성 근거나 champion 판정이 아니다. 다음 단계는 여러 승인
가설을 동일한 제한 실행기로 순차 처리하고, 실제 point-in-time 데이터 partition과
shadow forward evidence를 누적하는 것이다. 최소 두 executable Paper champion이 생기기
전에는 Allocation Manager를 구현하지 않는다.

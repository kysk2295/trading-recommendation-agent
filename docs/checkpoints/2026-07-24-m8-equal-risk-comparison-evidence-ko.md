# M8 Intraday Equal-risk Comparison Evidence 체크포인트

작성일: 2026-07-24 KST

## 구현 결과

commit `2737e111a710013c6caac4b7eaf125e4b0a0fd60`에서 개별 intraday
walk-forward·독립 Reviewer 뒤의 비교 증거 경계를 추가했다.

`run_intraday_equal_risk_comparison.py`는 2~3개의 completed experiment artifact와
각각의 독립 review artifact, global experiment ledger를 query-only로 읽는다. 다음
조건이 모두 맞을 때만 content-addressed mode-600 comparison artifact를 발행한다.

- experiment와 review 파일명, canonical bytes와 내부 artifact ID
- trial registration과 `started → completed` terminal chain
- completed event가 가리키는 exact experiment artifact
- 원 experiment에서 다시 계산한 Reviewer evidence·decision·reason 전체
- 고유 trial·strategy version·experiment·review identity
- 같은 causal CSV data version, persisted manifest SHA와 evaluator
- 같은 side cost, observed session, fold cardinality
- 현재 data contract, cost model, shadow portfolio policy
- intraday lane과 result strategy의 registered version identity
- 모든 source event 이후의 timezone-aware comparison 시각

review artifact loader도 공개 경계로 승격해 renamed, non-canonical, public,
symlink·hard-link source를 비교 전에 차단한다.

## 판정 계약

비교 artifact는 승자나 champion을 선택하지 않는다. 각 candidate가 최소 20개
out-of-sample session과 30개 trade를 모두 채우기 전에는 `collecting`이며, 2개
이상이 동일 조건으로 그 표본 하한을 채운 경우에만 `comparison_ready`다.

Reviewer의 `promote|hold|demote`는 원본 그대로 보존하지만 비교기가 이를 lifecycle
transition으로 바꾸지 않는다.

- automatic state change: `false`
- order authority change: `false`
- allocation change: `false`
- provider, credential, broker, account와 order mutation: `0`

따라서 이 artifact만으로 `challenger`, `shadow_champion`, `paper_champion` 또는
Paper arm을 만들 수 없다. 최소 두 executable Paper champion 전 Allocation
Manager 금지도 유지한다.

## 실제 CLI QA

기존 3전략 fixture의 exact experiment/review를 실제 isolated CLI로 처리했다.

- `--help`: exit `0`
- invalid time과 missing source: exit `1`, blocked report, comparison artifact `0`
- first/replay: exit `0/0`
- candidate: `3`
- status: `collecting`
- maturity blocker: `6`
- comparison artifact first/replay: `yes/no`
- artifact count: `1`
- artifact ID:
  `6ec781c512d4d9d661b25e339bc7e79300332bd5180471de6f8c14da0731df19`
- persisted file SHA-256:
  `f407e569d819ac090ef4c6d1113f97a71e061e7999bbacff57a10145c8fe5d5d`
- replay file SHA 동일: `true`
- artifact/report mode: `600`
- first/replay stderr: `0/0 bytes`

원천 review보다 이른 comparison 시각은 exit `1`로 차단되고, 같은 입력에서 시각만
원천 뒤로 옮기면 exit `0`으로 전환되는 인과성 toggle도 확인했다.

## 검증

- 신규 comparison 및 인접 research/reviewer/audit: `22 passed`
- 전체 pytest: `3583 passed in 221.89s`
- 전체 Ruff: 통과
- 전체 basedpyright: `0 errors, 0 warnings, 0 notes`
- changed-file format과 `git diff --check`: 통과
- production Python 최대 LOC: core `157`, models `155`, CLI `104`

## Actual terminal audit 결속

commit `3cfd7c7e966a75e08ae0c471aab46853837d2faa`에서 actual research
terminal audit을 schema v2로 올리고, 두 개 이상의 전략을 감사할 때 위 comparison
경계를 같은 terminal 실행 안에서 호출하도록 결속했다.

- terminal audit이 이미 검증한 exact experiment와 독립 review 객체를 comparison에
  전달하므로 임의 경로나 별도 candidate 입력을 받지 않는다.
- research 성공 receipt의 `completed_at_epoch`를 comparison 시각으로 사용하며, review
  원천보다 이른 receipt는 comparison artifact 발행 전에 차단한다.
- 2~3전략 audit은 comparison artifact ID와 `collecting|comparison_ready` 상태를
  content-addressed terminal artifact에 포함한다.
- 단일 전략 audit은 두 필드를 모두 비워 두고 보고서에 `not_applicable`을 명시한다.
- comparison artifact는 terminal audit과 같은 private output root에 mode `600`으로
  발행되며 lifecycle·주문·allocation 변경 권한은 계속 없다.

TDD에서 먼저 comparison 필드 결손을 확인한 뒤 1전략, 3전략, 이른 receipt 차단과
실제 subprocess happy path를 추가했다. 변경 뒤 audit 타깃은 `5 passed`, 전체
pytest는 `3583 passed in 221.59s`, 전체 Ruff와 basedpyright
`0 errors, 0 warnings, 0 notes`, CLI help/bad/happy가 통과했다.

## 다음 운영 경계

이 구현은 예약된 2026-07-24 및 2026-07-27 frozen actual-research payload에 소급
주입하지 않았다. clean actual session에서 causal CSV, READY v2 manifest, 세
completed walk-forward와 독립 review가 실제 생성된 뒤 exact
`3cfd7c7e966a75e08ae0c471aab46853837d2faa` 이상 runtime으로 terminal audit을
실행해야 comparison까지 한 terminal artifact에 결속된다.

실제 비교 표본이 성숙하더라도 DSR/PBO, parameter plateau, SIP 또는 동등
consolidated feed와 broker/shadow Paper evidence는 별도 승격 검토 입력이다.

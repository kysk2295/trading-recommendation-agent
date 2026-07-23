# M8 Walk-forward OOS outcome trace 체크포인트

작성일: 2026-07-24 KST

## 발견한 결손

기존 `intraday_walk_forward_v1` experiment artifact는 observed session·trade 수,
평균·PF·누적수익·MDD와 bootstrap CI 집계만 보존했다. 개별 OOS session의
gross/net return block과 bootstrap seed가 남지 않아 Reviewer가 집계치를 읽을 수는
있어도 DSR/PBO 같은 과최적화 진단을 독립적으로 재계산할 수 없었다.

## 구현

commit `303eb8454c3b360cf53504411c332d0648496c3e`에서
`intraday_walk_forward_v2`와 experiment artifact schema v2를 추가했다.

- 최대 60개 OOS session을 날짜순으로 모두 보존하며 거래가 없는 session도 빈
  outcome block으로 남긴다.
- 각 완결 거래의 gross return과 편도 cost를 적용한 net return을 원래 terminal
  순서대로 보존한다.
- bootstrap sample 수와 deterministic seed `20260722`를 artifact에 결속한다.
- trace에서 trade 수, gross/net 평균, PF, 누적수익, MDD와 거래일 block-bootstrap
  CI를 다시 계산해 집계치와 다르면 artifact validation에서 차단한다.
- gross/net 관계도 exact side cost 공식으로 다시 계산하므로 trace와 집계치를 함께
  바꾼 self-consistent cost 위조를 차단한다.
- outer artifact, payload와 result schema가 모두 같은 v1 또는 v2인지 검증한다.

기존 `intraday_walk_forward_v1`은 aggregate-only schema v1을 그대로 생성·읽으며
legacy artifact를 재작성하지 않는다. actual input binding과 repository example은
새 evaluator v2를 사용한다.

이 변경은 DSR/PBO 통과 판정을 만들지 않는다. 독립 통계 진단이 사용할 수 있는
immutable OOS return 입력을 처음으로 보존한 단계다.

## TDD 및 CLI QA

RED에서 다음 결손을 먼저 확인했다.

- walk-forward result가 schema v1이고 outcome trace가 없음
- outer experiment artifact가 schema v1
- evaluator v1/v2 분리가 없어 legacy generation 계약을 선택할 수 없음
- bootstrap CI와 cost 관계를 self-consistent하게 바꿔도 trace validator가 거부하지
  않음

GREEN 뒤 실제 격리 CLI에서 다음을 확인했다.

- `--help`: exit `0`
- missing manifest: exit `1`, blocked report, external mutation `0`
- first/replay: exit `0/0`
- trial: `3`
- experiment/review artifact: `3/3 → 0/0`
- outer/payload/result schema set: `{2}`
- evaluator set: `{intraday_walk_forward_v2}`
- session outcome block: `3`
- artifact/review mode: `600`
- first/replay stdout/stderr: 모두 `0 bytes`

## 검증

- outcome trace와 인접 research/audit: `65 passed`
- 전체 pytest: `3585 passed in 221.56s`
- 전체 Ruff: 통과
- 전체 basedpyright: `0 errors, 0 warnings, 0 notes`
- `git diff --check`: 통과
- external provider, credential, broker, account와 order mutation: `0`

## 운영 경계

이미 예약된 2026-07-24 및 2026-07-27 frozen research runtime에는 이 schema를
소급하지 않았다. exact
`303eb8454c3b360cf53504411c332d0648496c3e` 이상 runtime으로 새 data version의
trial을 실행해야 v2 trace가 생성된다. 다음 구현 경계는 여러 exact v2 trial의
동일 session matrix와 global trial count를 사용해 DSR/PBO evidence를 계산하고,
충분한 variant·session이 없으면 명시적으로 `collecting`으로 닫는 것이다.

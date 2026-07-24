# M8 clean actual session 누적 plan 체크포인트

작성 시각: 2026-07-24 09:55 KST

기능 커밋:
`de5f3956ad68e403766c20f571a340b15882fe67`

## 발견한 성숙 차단

strict session catalog는 전달받은 후보를 각각 기존 replay 품질 게이트로 감사하고,
blocked session을 receipt에 보존하면서 clean session만 최신 순으로 선택한다. 그러나
2026-07-24와 2026-07-27 actual research payload는 각각 현재 날짜의
`--session-dir` 하나만 전달했다.

intraday DSR/PBO 진단은 여러 날짜의 experiment artifact를 합치지 않는다. 같은
실행에서 생성된 전략별 현재 artifact의 `session_outcomes`를 동기 행렬로 사용한다.
따라서 기존 예약은 날짜가 늘어도 매번 한 세션짜리 artifact만 만들고, 최소 20개
동기 OOS session 조건에 도달할 수 없었다.

## 누적 입력 계약

planned actual research CLI에 `--session-root`를 추가했다. 기존 명시적
`--session-dir`과 상호배타적이다.

- root의 direct child 중 정확한 ASCII `YYYYMMDD` 디렉터리만 후보로 사용한다.
- required session date보다 미래인 디렉터리와 symlink는 제외한다.
- 최근 최대 366개 후보를 날짜순으로 exact run plan에 동결한다.
- required date 디렉터리가 후보 집합에 없으면 plan publication 전에 차단한다.
- plan은 후보 최대 366개를 보존하지만 catalog와 materializer는 기존
  `max_sessions <= 60`, 현재 예약 `20`, `max_bars <= 100000`을 유지한다.
- blocked session은 삭제하거나 0수익으로 바꾸지 않고 catalog audit에 남긴다.
- required current session이 strict clean이 아니면 기존 gate가 dataset, READY
  foundation, trial과 Reviewer를 계속 차단한다.

기존 명시적 session-dir plan과 plan schema v2/v3 replay는 바꾸지 않았다.

## TDD와 CLI QA

RED:

- cumulative root resolver 부재: `AttributeError`
- plan candidate 366개 동결 거부: Pydantic validation failure
- 7자리 숫자 날짜 디렉터리 오인식: expected candidate set 불일치

GREEN:

- actual plan/catalog/overfit 타깃: `30 passed`
- 전체 pytest: `3593 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- changed-file no-excuse audit: 위반 `0`

actual-shaped CLI는 blocked fixture 1개와 clean fixture 1개를 같은 root에 두고
실행했다.

- help/bad/happy/replay: `0/2/0/0`
- plan candidate session: `2`
- catalog selected/blocked: `1/1`
- selected date: `2026-07-14`
- plan/outcome schema: `3/2`
- plan/catalog mode: `600/600`
- trial/review first run: `1/1`, replay 신규 `0/0`
- Reviewer: `hold`
- provider, credential, account, broker, order mutation: `0`

## 누적 current-code 예약

exact clean detached runtime은
`/private/tmp/trading-agent-actual-cumulative-de5f395`이고 기능 커밋 SHA와
정확히 같다.

새 누적 job 네 개를 먼저 등록하고 실제 대기 상태를 확인한 뒤, 실행 전이던 기존
단일-session schema-v2 research/audit label 네 개만 제거했다. 기존 payload,
runner와 mode-600 빈 로그는 보존했다. upstream strict closeout은 유지했다.

| 실행 시각 | label | PID | 계약 |
|---|---|---:|---|
| 2026-07-25 05:18 KST | `ai.trading-agent.post-closeout-research-cumulative-v1-20260724` | 8716 | strict current closeout 뒤 root 후보 감사 → cumulative causal CSV → READY manifest → 3전략 schema v2 trial·Reviewer |
| 2026-07-25 06:50 KST | `ai.trading-agent.actual-research-cumulative-v1-audit-20260724` | 8722 | exact plan·catalog·CSV SHA·foundation·trial/review·comparison·DSR/PBO query-only 감사 |
| 2026-07-28 05:40 KST | `ai.trading-agent.post-closeout-research-cumulative-v1-20260727` | 8728 | 7월 27일 required clean session을 포함한 같은 cumulative vertical |
| 2026-07-28 06:25 KST | `ai.trading-agent.actual-research-cumulative-v1-audit-20260727` | 8733 | 다음 누적 artifact terminal schema v3 감사 |

payload/runner는 mode `700`, stdout/stderr는 mode `600`, `0 bytes`다.
receipt와 claim은 아직 없다. payload 네 개 모두 `zsh -n`, dry-run, bad input
exit `2`를 통과했고 research dry-run은 `session_source=cumulative_root`를
표시한다.

KR session PID 94276과 Hermes PID 31663은 변경·중단·재시작하지 않았다.

이는 clean 실제 session, READY foundation, 통계 성숙, 성과, champion 또는 Paper
권한의 성공 주장이 아니다. 실제 closeout이나 required session이 실패하면 누적
후보에 과거 clean session이 있어도 현재 run 전체가 계속 차단된다. 최소 20개
동기 session과 전략별 30 trades 전에는 DSR/PBO와 equal-risk comparison은
`collecting`이다. executable Paper champion 두 개 전에는 Allocation Manager를
활성화하지 않는다.

# M8 KIS EOD 마지막 봉 복구·clean-session rollover 체크포인트

## 2026-07-23 strict terminal

예약된 closeout은 2026-07-24 05:01 KST에 mode-600 receipt를 남기고 exit `1`로
끝났다. strict report는 `forward_progress_blocked`였고 실패 cycle 삭제 `0`,
quality gate 완화 `false`, provider/account/order operation `0`을 보존했다.

최종 정규장 원장은 다음과 같다.

- watch/ranking/retry/candidate input cycle: `355/355/355/355`
- watch failure: `1`
- KIS read retry/recovery/failure: `1115/1114/1`
- 원인: 02:44 KST `AMS/TSLZ` 분봉 GET의 연속 `500`
- post-session metrics/research/adaptive evaluation: 모두 exit `0`
- EOD catch-up: 후보 `29`, 완전 `27`, 실패 `2`

EOD retry event 세 건은 모두 HTTP `500 → 200`으로 복구됐지만 `NAS/LMTL`과
`AMS/CIFU`의 성공 응답은 정규장 마지막 `15:59` 봉 대신 `15:58`까지만 반환했다.
두 관측은 각각 119개 bar와 `장마감 마지막 완료 봉 없음`으로 남았고 EOD catch-up은
exit `1`을 유지했다. HTTP 성공을 complete로 바꾸거나 15:58을 마지막 봉으로
인정하지 않았다.

## semantic-lag bounded recovery

commit `581eebc08965a647c9b84374e9fade98ccc8a75a`에서 최초 전체 후보 조회 뒤 exact
`장마감 마지막 완료 봉 없음`인 후보만 2초, 5초에 batch 재조회한다.

- 모든 후보를 매번 다시 조회하지 않는다.
- HTTP/transport/다른 semantic 오류는 이 retry 대상이 아니다.
- 두 번 뒤에도 15:59 봉이 없으면 기존 오류와 nonzero terminal을 유지한다.
- 첫 누락 응답에 저장된 15:58 bar는 삭제하지 않고, 뒤의 15:59 bar를 append한다.
- 최대 provider semantic attempt는 최초 포함 3회다.

fixture는 첫 응답 15:58, 두 번째 15:59에서 `complete=1`, request `2`, wait `[2.0]`
으로 복구됐다. 세 응답 모두 15:58인 fixture는 request `3`, wait `[2.0, 5.0]`,
failure `1`을 그대로 유지했다.

## 다음 actual session 교체

기존 2026-07-24 forward job은 17:00 KST 시작 전이고 watch output, receipt, claim이
모두 없었다. 기존 waiting label만 제거하고 exact clean detached runtime
`/private/tmp/trading-agent-forward-20260724-581eebc`로 다시 등록했다.

- label: `ai.trading-agent.us-forward-20260724`
- PID: `43849`
- state/runs: `running/1`
- exact runtime SHA: `581eebc08965a647c9b84374e9fade98ccc8a75a`
- 시작: `2026-07-24 17:00 KST`
- KIS server attempts: 최대 `4`
- EOD last-bar semantic attempts: 최대 `3`
- cycle cadence: start-to-start `60s`
- wrapper mode: `700`
- stdout/stderr mode·bytes: `600/0`
- receipt/claim: 없음

기존 2026-07-23 session, KR finalizer와 Hermes는 변경하지 않았다. 새 예약도 아직
clean session, causal CSV, READY foundation, trial 또는 Reviewer 성공 증거가 아니다.
최소 두 executable Paper champion 전에는 Paper arm과 Allocation Manager를 열지 않는다.

## 검증

- focused KIS EOD/server recovery: `18 passed`
- 전체 pytest: `3563 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- EOD CLI `--help`: exit `0`
- bad `--max-pages 0`: exit `2`, provider operation `0`
- runner `zsh -n`, dry-run, bad input: 통과
- account/order/allocation mutation: `0`

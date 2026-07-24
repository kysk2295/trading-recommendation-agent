# KR Opportunity 장중 Watch와 독립 세션 예약 체크포인트

## 결과

2026-07-24 KR M3가 09:05 source preflight 실패 뒤 종가까지 다시 수집하지 않은 원인을
단발 orchestration으로 확정했다. 기존 strict one-shot과 네 source 품질 게이트는
변경하지 않고, 새 exact cycle ID로 장중 반복하는 bounded watch를 추가했다.

기존 7월 27일 one-shot 예약 프로세스는 변경·중단·재시작하지 않았다. 새 code-coupled
strategy version과 분리된 session root를 쓰는 독립 watch chain 및 장후 verifier를
추가 예약했다.

## 구현 계약

`run_kr_same_cycle_opportunity_watch.py`는 다음을 보장한다.

- source preflight 차단과 완전한 four-source `no_opportunity` cycle을 모두 보존한다.
- 재시도마다 새 cycle ID와 분리된 collection/operator output을 사용한다.
- 같은 KST session date와 timezone-aware deadline을 요구한다.
- poll 간격과 최대 시도 수를 모두 제한한다.
- 정확히 한 Opportunity가 있는 cycle에서만 exit 0과 cycle ID 한 줄을 발행한다.
- child stdout/stderr는 cycle별 mode-600 로그로 격리한다.
- deadline 또는 최대 시도 소진은 mode-600 report와 non-zero exit로 닫는다.
- 국내 계좌·주문·포지션 endpoint와 mutation은 사용하지 않는다.

## Exact 운영 결속

- 구현 commit:
  `fbcb34d4e24bea87aec28c1e04434fef9b7f95a3`
- frozen runtime:
  `/private/tmp/trading-agent-kr-m3-20260727-fbcb34d`
- runtime 상태:
  exact HEAD, porcelain empty, watch CLI executable
- rollover bundle SHA-256:
  `e3996d1e95a6fcfb6bf6e2a095f37f570f459e0480d24b810048ce7389540d31`
- policy SHA-256:
  `536e1d2f5e560a0b1a2d69e4db93006e9108927a5538f3aa908a9e690bbf0f53`
- Opportunity version:
  `kr-theme-keyword-projection-v1-code-5ae012a61bf72351`
- day version:
  `kr-theme-leader-vwap-reclaim-v1-code-5ae012a61bf72351`
- ledger 신규 version:
  `2`
- rollover first/replay exit:
  `0/0`
- replay 신규/재사용:
  `0/2`
- bundle, policy, report mode:
  `600`

## 2026-07-27 예약

| KST 시각 | label | 동작 |
|---|---|---|
| 08:55 | `ai.trading-agent.kr-m3-watch-20260727` | calendar, composite, trial 등록 |
| 09:00 | 같은 chain | shadow trial start |
| 09:05~15:20 | 같은 chain | 5분 간격, 최대 75개 strict four-source Opportunity cycle |
| READY 직후~15:32 | 같은 chain | onboarding 후 완료 1분봉 day-agent tick |
| 15:32 | 같은 chain | terminal 및 session verification |
| 15:45 | `ai.trading-agent.kr-m3-watch-post-session-verify-20260727` | terminal·delivery·Reviewer·lifecycle 독립 exact replay |

두 label은 등록 뒤 각각 `state=running`, `runs=1`,
`last exit code=(never exited)`였다. primary runner의 claim은 mode 700이고 네 stdout/stderr
로그는 mode 600, 크기 0으로 확인했다. 기존
`ai.trading-agent.kr-m3-20260727`도 그대로 `running`이며 별도 session root를 사용한다.

## 검증

- RED: watch CLI 부재로 test collection error
- 관련 Opportunity/watch 테스트:
  `17 passed`
- 전체 pytest:
  `3654 passed`, 기존 `tests/test_grok_task_runner.py` 오프라인 환경 테스트 `5 failed`
- Ruff 전체:
  pass
- basedpyright 전체:
  `0 errors, 0 warnings, 0 notes`
- CLI manual QA:
  help `0`, naive deadline `2`, fixture READY `0`
- manual READY:
  stdout exact cycle ID 한 줄, Opportunity `1`, report/log mode `600`
- runner, verifier payload/wrapper:
  `zsh -n` pass, mode `700`
- account/order/position mutation:
  `0`

## 다음 판정

7월 27일 watch session이 clean terminal을 만들기 전에는 actual causal dataset이나 READY
foundation으로 승격하지 않는다. 모든 cycle이 blocked 또는 `no_opportunity`이면 실패
증거를 삭제하지 않고 trial을 censored로 닫는다.

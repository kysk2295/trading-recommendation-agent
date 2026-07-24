# M8 strict closeout→actual research→terminal audit 예약 체크포인트

## 발견한 운영 결손

2026-07-24 full-session forward는 KIS 네 번째 server attempt와 60초 cycle-start
cadence를 포함한 SHA `3c476d5390b39c7db252216f2191c6d0d4b8b6fb`로 교체돼
있었다. 그러나 downstream 두 예약은 더 오래된 경로였다.

- dataset job: producer provenance SHA `70e7d94`, strict closeout 선행조건 없음
- research job: SHA `70e7d94`, `--dataset-producer-commit-sha`와
  `--prerequisite-receipt/report` 없음
- independent exact persisted-manifest terminal audit: 예약 없음

dataset materializer 자체의 품질 검사는 유지되지만, 최신 제품 계약인
`300~390` 동등 cardinality strict closeout과 persisted manifest·READY foundation·
trial·Reviewer 독립 재검증이 전체 체인에 결속되지 않은 상태였다.

## 교체 원칙

KR finalizer, Hermes, 실행 중인 2026-07-23 forward/closeout/research/audit와
2026-07-24 forward/progress/preflight/finalizer는 변경하지 않았다. 기존
`intraday-dataset-20260724`, `intraday-research-20260724` 두 job만 다음 증거를
확인한 뒤 research부터 역순으로 제거했다.

- 상태: `running`, `runs=1`, payload exit 이력 없음
- dataset/research stdout·stderr: 모두 `0 bytes`
- dataset report, actual research report와 plan: 없음

따라서 stale payload, dataset, foundation, trial 또는 review는 생성되지 않았다.
기존 runner 파일과 빈 로그는 사고 증거로 보존했다.

## exact runtime과 새 체인

clean detached runtime
`/private/tmp/trading-agent-actual-chain-20260724-8ef5904`에
`8ef5904df2589f95cc80013e068d2a2cbdb4c96f`를 고정했다. dataset producer는
이 exact SHA이고, 세 strategy의 frozen code version은 기존 source-backed 계약의
`70e7d94dd0f56bc40b9fe602de22657c38f8e844`로 별도 보존한다.

```text
current 2026-07-24 strict forward
→ minimum 300-cycle strict post-session closeout
→ causal CSV + producer-bound receipt/catalog
→ exact input SHA + 1~3 READY data foundations
→ VWAP/HOD/Gap-and-Go source-backed walk-forward
→ independent Reviewer
→ exact persisted manifest terminal audit
```

| 시각 | launchd label | 계약 | 관측 PID |
|---|---|---|---:|
| 2026-07-25 05:06 KST | `ai.trading-agent.forward-post-session-20260724-v2` | watch 종료를 기다린 뒤 minimum `300`, 네 cycle cardinality 동등, 실패 삭제 `0`, gate 완화 `false` | 80298 |
| 2026-07-25 05:18 KST | `ai.trading-agent.post-closeout-research-20260724-v2` | closeout exit `0` receipt와 strict report를 요구하고 dataset→foundation→3전략 trial→Reviewer 실행 | 80304 |
| 2026-07-25 05:35 KST | `ai.trading-agent.actual-research-terminal-audit-20260724-v2` | research receipt를 06:30까지 기다린 뒤 plan·persisted manifest·CSV/receipt·READY foundation·trial/review를 query-only 재검증 | 80309 |

세 wrapper는 모두 atomic claim, mode-600 completion receipt와 종료 후 self-remove를
사용한다. 연구 단계는 closeout receipt를 05:35까지 기다리고, audit은 research
receipt를 06:30까지 기다린다. 앞 단계가 실패하거나 늦으면 성공으로 추정하지 않고
nonzero receipt와 blocked report를 남긴다.

## 수동 QA와 등록 증거

- frozen runtime HEAD: exact `8ef5904`, clean
- 세 core CLI `--help`: exit `0`
- payload `zsh -n`: exit `0`
- payload `--dry-run`: exit `0`, strict/no broker mutation 계약 출력
- payload bad input: 모두 exit `2`, stdout `0`
- scheduler bad label: exit `2`, artifact 생성 없음
- payload/wrapper mode: `700`
- stdout/stderr mode: `600`
- 등록 직후 세 label: `state=running`, `runs=1`
- 등록 직후 receipt: `0`, stdout/stderr: 모두 `0 bytes`
- external provider, credential, account, order mutation: `0`

실제 결과는 아직 생성되지 않았다. clean session이 아니면 partial CSV나 historical
trial을 발행하지 않는다. READY audit이 성공해도 Reviewer 기준은 별개이며 Paper
champion이나 주문 권한을 자동 생성하지 않는다. executable Paper champion 두 개
전에는 Allocation Manager를 계속 비활성으로 유지한다.

## 실행 전 strict 후속 예약 복구

2026-07-24 14:31 KST 재감사에서 05:06 strict closeout job은 기존 PID `80298`,
run count `1`, terminal exit 없음으로 유지됐지만, 그 뒤의 05:18 actual research와
05:35 terminal audit label은 launchd에 없었다. 두 job의 completion receipt·claim,
stdout·stderr 내용과 actual research artifact는 모두 없었으므로 이미 실행된
결과를 덮거나 재해석할 상태가 아니었다. 앞서 의도적으로 폐기한 오래된
`intraday-dataset-20260724`와 `intraday-research-20260724`는 복구하지 않았다.

기존 exact `8ef5904` clean runtime과 mode-700 wrapper를 다시 검증한 뒤 최신 strict
후속 두 label만 재등록했다.

- `ai.trading-agent.post-closeout-research-20260724-v2`: PID `15780`
- `ai.trading-agent.actual-research-terminal-audit-20260724-v2`: PID `15782`
- 두 label: `state=running`, `runs=1`, terminal exit 없음
- payload `zsh -n`: 통과
- payload dry-run/bad input: `0/2`
- stdout·stderr: 모두 mode `600`, `0 bytes`
- closeout, forward, KR finalizer/verifier, Hermes 변경·재시작: `0`

research payload는 기존대로 closeout의 exact exit-0 receipt와 strict report를
요구하고, terminal audit은 research receipt와 persisted manifest·READY
foundation·trial·review를 독립 검증한다. 예약 복구는 실패를 성공으로 만들거나
dataset gate, Reviewer 또는 allocation 권한을 완화하지 않는다.

## Schema v2 comparison 후속 재감사 예약

기존 2026-07-24 및 2026-07-27 research/audit payload는 실행 전 frozen 상태를
그대로 보존했다. 이후 배포된 terminal audit schema v2가 2~3전략의 exact
equal-risk comparison artifact ID와 상태를 같은 terminal artifact에 결속하므로,
기존 job을 제거·교체하지 않고 query-only 후속 재감사 두 개를 추가했다.

clean detached runtime
`/private/tmp/trading-agent-terminal-comparison-audit-91ff5d2`는 exact
`91ff5d2900d9c06d09c6cdebaa3fe4d1df745d5d`이며 clean status를 payload 실행
직전에 다시 확인한다. audit runtime과 연구 dataset producer SHA는 서로 다른
필드로 보존한다.

| 시각 | launchd label | 연구 producer | 관측 PID |
|---|---|---|---:|
| 2026-07-25 06:35 KST | `ai.trading-agent.actual-research-comparison-audit-20260724` | `8ef5904df2589f95cc80013e068d2a2cbdb4c96f` | 38652 |
| 2026-07-28 06:10 KST | `ai.trading-agent.actual-research-comparison-audit-20260727` | `bc400690febe0fb376b68594290a20ea55764b34` | 38654 |

두 job은 기존 research completion receipt와 plan/report를 읽고 별도
`exact-91ff5d2-schema-v2` output root에만 발행한다. 성공 receipt가 아니거나 plan,
CSV, persisted manifest, READY foundation, completed trial/review 또는 동일 위험
계약이 맞지 않으면 audit은 nonzero로 차단한다. 실패를 새 research 실행이나 부분
artifact로 복구하지 않는다.

등록 QA는 다음과 같다.

- payload/runner `zsh -n`: 통과
- payload dry-run: exit `0`, schema `2`, strict, mutation `false`
- payload bad input: exit `2`, stdout `0 bytes`
- frozen runtime audit CLI `--help`: exit `0`, stderr `0 bytes`
- payload/runner mode: `700`
- stdout/stderr mode: `600`, 등록 직후 `0 bytes`
- 두 label: `state=running`, `runs=1`
- 등록 직후 receipt/claim: 없음
- external provider, credential, broker, account와 order mutation: `0`

## PEP 723 독립 실행 복구

운영 수동 QA에서 project environment가 아닌 각 CLI의 PEP 723 isolated environment로
실행하면 shared KIS model의 `httpx2` import가 누락되어 `--help`부터 실패하는 결손을
확인했다. 예약 payload는 explicit project Python을 사용하므로 기존 frozen job의 실행
계약은 바뀌지 않지만, 공개 CLI의 독립 복구·수동 재실행 계약은 깨진 상태였다.

다음 세 작은 커밋으로 actual research vertical의 공개 표면을 모두 복구했다.

- `5e6c4fd2e6e7e5293ec5f31c7b9626abb2109cb7`:
  causal dataset과 multi-strategy research loop
- `b67a4f929827cc19abfb260c56326fb4198f7679`:
  actual coordinator, planned coordinator와 terminal audit
- `172b0484c4e20e0de52703b38e833c5fa77f2a53`:
  dataset catalog와 READY input binding

PEP 723 metadata를 구조적으로 검사하는 회귀 테스트를 추가했다. 실제 isolated CLI
QA에서 다섯 계층의 `--help`가 모두 exit `0`, invalid binding이 exit `2`였고, 완전한
fixture는 causal CSV SHA, READY manifest, trial `1`, Reviewer `hold`까지 exit `0`으로
완료했다. 전체 `3561 passed`, Ruff 통과, basedpyright `0 errors, 0 warnings,
0 notes`였고 외부 provider, credential, account, order mutation은 `0`이다.

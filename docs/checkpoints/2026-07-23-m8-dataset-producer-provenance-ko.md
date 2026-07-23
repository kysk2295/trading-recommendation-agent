# M8 actual dataset producer provenance 체크포인트

작성 시각: 2026-07-23 18:33 KST

## 닫은 운영 결손

7월 23일 actual dataset job은 strict source와 CSV content SHA를 보존했지만
`/private/tmp/trading-agent-forward-20260723-0c7dc575` 복사 디렉터리에서 실행될
예정이었다. 이 경로는 Git worktree가 아니었고 wrapper에도 expected SHA와 clean
runtime 검사가 없었다. 따라서 같은 CSV bytes를 만들더라도 어떤 materializer
commit이 READY receipt를 발행했는지 운영 증거로 확정할 수 없었다.

교체 전에 `outputs/intraday_research/actual-20260723`과
`actual-20260724`는 모두 없었으므로 구 schema READY dataset이나 historical trial은
발행되지 않았다.

## 구현 계약

commit `70e7d94dd0f56bc40b9fe602de22657c38f8e844`에서 다음 경계를 추가했다.

- dataset·catalog CLI는 정확한 40자리 lowercase Git SHA인
  `--producer-commit-sha`를 필수로 받는다.
- `IntradayResearchDatasetReceipt` schema v2는 CSV SHA, source-session SHA와 함께
  `producer_commit_sha`를 content-addressed payload에 포함한다.
- 잘못된 producer SHA는 source read와 publication 전에 차단한다.
- cumulative catalog와 one-shot actual coordinator가 같은 producer SHA를
  materializer에 전달한다.
- `IntradayResearchInputBindingReceipt` schema v2는 exact dataset receipt SHA와
  `dataset_producer_commit_sha`를 함께 보존한다.
- v2 research manifest의 exact CSV SHA와 READY foundation SHA는 기존대로
  유지되며 binding receipt를 통해 producer commit까지 재생할 수 있다.
- 품질 gate, 실패 cycle, entitlement, source queue, Reviewer와 전략 기준은
  완화하지 않았다.

## 실제 예약 교체

clean detached runtime
`/private/tmp/trading-agent-dataset-provenance-20260723-70e7d94`를 exact commit에
고정했다. 7월 23일과 24일 dataset/catalog wrapper는 실행 직전에 runtime HEAD와
dirty 상태를 확인하고 같은 SHA를 CLI에 전달한다. research wrapper도 같은 runtime과
code version을 사용해 schema-v2 receipt를 읽도록 교체했다.

의존 race를 막기 위해 각 날짜에서 research 대기 job을 먼저 제거하고 dataset job,
research job 순서로 다시 등록했다.

- 2026-07-23 dataset/research PID: `99876` / `99878`
- 2026-07-24 catalog/research PID: `99883` / `99884`
- 네 job 모두 run count `1`, state `running`
- wrapper `zsh -n`, dry-run exit `0`, mode `700`
- launchd stdout/stderr mode `600`

US forward PID `62716/29095`, 조기·후반 진행 감사 PID `84727/84825`, 기존 KR
finalizer와 Hermes PID `31663`은 변경하지 않았다. provider, account, broker와
order mutation은 `0`이다.

## TDD·수동 QA

- 첫 RED: dataset request에 producer commit 필드가 없어 실패
- 첫 GREEN: dataset receipt schema v2에 exact commit 보존
- 둘째 RED: downstream binding receipt에 dataset producer commit이 없어 실패
- 둘째 GREEN: binding receipt schema v2가 commit을 보존
- focused: `29 passed`
- 전체 pytest: `3432 passed`
- Ruff 전체: pass
- basedpyright: `0 errors, 0 warnings, 0 notes`
- no-excuse: pass

CLI `--help`에서 새 필수 옵션을 확인했다. 잘못된 SHA는 exit `1`, CSV `0`건이었다.
완전 fixture의 dataset/catalog/binding은 모두 exit `0`, dataset/binding schema
`2/2`, producer commit 일치, causal bar `384`, 모든 보고서·receipt mode `600`이었다.

이 체크포인트는 producer provenance를 닫았을 뿐 clean actual session이나 성과를
만들지 않는다. 실제 정규장 세션이 strict gate를 통과한 뒤에만 exact CSV, READY
foundation, walk-forward와 독립 Reviewer 결과가 생성된다.

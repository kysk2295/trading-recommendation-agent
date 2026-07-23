# M8 actual research 재시작 안전 run plan 체크포인트

## 닫은 결손

실시간 dataset watcher가 source queue와 날짜별 strategy version을 shell 상수로 직접
전달했다. 첫 trial 뒤 queue snapshot은 바뀌므로 프로세스 재시작 때 입력이 달라질 수
있었고, 다음 세션에는 같은 전략의 날짜별 새 version이 만들어질 위험이 있었다.

`run_planned_intraday_actual_research.py`는 actual coordinator 전에 날짜별 run key의
실행 계약을 private immutable plan으로 먼저 고정한다.

```text
run key + candidate sessions + required date
+ stable strategy versions + runtime SHA + registered_at
+ cost/resource budgets + local paths
→ current experiment ledger의 exact queue projection
→ immutable run plan
→ strict catalog → actual binding → walk-forward → Reviewer
```

## 불변 계약

- 최초 run key만 현재 ledger에서 source queue를 투영하고 exact mode-600 artifact를
  발행한다.
- 같은 run key 재시작은 ledger가 trial로 바뀐 뒤에도 기존 plan과 queue를 로드한다.
- 같은 run key에 session, required date, version, SHA, 등록시각, 예산 또는 경로가
  달라지면 coordinator와 trial mutation 전에 차단한다.
- 다음 run key는 그 시점의 최신 queue를 새로 고정하지만 동일한 strategy version
  이름을 사용한다.
- plan ID는 canonical plan content SHA-256이며 plan filename은 validated run key에
  일대일로 결속된다.
- observed time은 재시작 시점의 현재 UTC를 쓰지만 연구 입력·전략·비용 계약에는
  포함되지 않는다.
- lifecycle, champion, allocation, broker/account/order mutation은 없다.

## 검증

- library:
  - first run: plan/queue created `true/true`
  - same run key replay: plan/queue created `false/false`
  - replay 신규 trial/review `0/0`, strategy version/trial `1/1`
  - next run key + 2-session data: strategy version/trial `1/2`
  - same run key spec drift: trial mutation 없이 차단
- CLI:
  - `--help`: run key, plan/queue dir, required date, strategy binding 노출
  - malformed strategy binding: exit `2`
  - first/replay: exit `0/0`
  - plan create/reuse `true/false`
  - replay 신규 trial/review `0/0`
  - plan, queue, dataset, binding, trial, review, report mode `600`
- 관련 테스트: `4 passed`
- 전체 pytest: `3418 passed`
- Ruff와 basedpyright: `0 errors, 0 warnings`
- provider, credential, account, broker와 order mutation: `0`

이 plan은 예약 상태를 성과나 readiness로 바꾸지 않는다. strict current-session gate가
통과한 경우에만 동일 coordinator가 실제 causal CSV, READY foundation, trial과 독립
Reviewer 증거를 만든다.

## 운영 연결

7월 23일과 24일 dataset 후속 research watcher는 이 CLI가 있는 frozen runtime
`e095bef9cf3d90dd38ec6f31d1fc8009b3f92a4f`와 날짜별 run key를 사용하도록
교체했다. 두 watcher는 같은 세 고정 strategy version을 사용하며 7월 24일은 두 날짜
session을 누적 감사한다. 두 job은 run count `1`, state `running`으로 dataset
terminal을 기다리고 있고, 기존 KR finalizer와 Hermes를 포함한 다른 실시간 job은
변경하지 않았다.

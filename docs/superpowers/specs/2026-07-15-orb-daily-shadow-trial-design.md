# ORB 일일 Shadow Trial 설계

날짜: 2026-07-15

상태: 구현 기준

## 1. 목표

ORB의 한 NYSE 정규 세션을 사전등록된 `shadow_forward` trial 하나로 표현하고, 장후 exact daily research·adaptive·lane snapshot·Reviewer evidence를 검증해 `completed`, `censored`, `failed` 중 하나의 immutable terminal event로 닫는다.

이 단계는 전략 승격이나 주문 실행이 아니다. global experiment ledger의 연구 계보를 실제 일일 forward-validation loop에 연결하는 control-plane 작업이다.

## 2. 선택한 단위

세 가지 단위를 비교했다.

1. 60거래일 trial 하나: terminal event가 한 번뿐이므로 일별 실패와 검열을 별도 보존할 수 없어 기각한다.
2. 한 trial에 일별 non-terminal event 추가: 현재 검증된 `started → terminal` chain과 schema를 다시 설계해야 하므로 기각한다.
3. 거래일당 trial 하나: 각 세션을 독립적으로 preregister·terminal 처리하고 기존 adaptive evaluator가 exact scope의 장기 표본을 집계할 수 있어 채택한다.

trial ID는 `orb-shadow-YYYYMMDD-<strategy-version-sha256-12>`로 결정론적으로 만든다. 같은 날짜라도 strategy version이 다르면 다른 trial이며, 다른 lane·scope·비용·코드 결과를 사후 혼합하지 않는다.

## 3. 사전등록 계약

등록은 해당 NYSE 세션 정규장 open 전에만 신규 생성할 수 있다. 이미 exact trial이 있으면 open 이후 재실행도 최초 `registered_at`을 재사용하는 replay로 허용한다.

등록 서비스는 다음을 다시 검증한다.

- current canonical intraday manifest와 ORB experiment scope
- global ledger의 canonical ORB hypothesis와 strategy version
- strategy version의 code·parameter·data·cost·portfolio 계약
- 계획 세션에 유효한 lifecycle state 존재와 `rejected` 아님
- 실행 중 checkout code version과 등록된 strategy version code version의 exact 일치
- `planned_start == planned_end == session_date`

`ExperimentTrialRegistration.data_version`은 미래 파일 내용을 예측한 checksum이 아니다. 다음 prospective 계약의 canonical JSON SHA-256이다.

- `CURRENT_DATA_CONTRACT`
- required artifact path 목록
- optional artifact path 목록

evaluator version과 feed entitlement는 기존 registration 필드로 별도 고정한다. 실제 세션 파일의 content-bound `DailyResearchRecord.data_version`은 장후 terminal artifact로 결속한다.

evidence budget은 다음 네 개를 각각 한 개로 고정한다.

- daily research record
- adaptive evaluation
- lane daily snapshot
- lane review event

## 4. 세 단계 상태기계

```text
pre-open register
  → regular-session start
  → post-close completed | censored | failed
```

### Register

- global ledger에 trial registration만 append한다.
- broker, credential, HTTP와 execution DB를 읽지 않는다.
- 신규 등록 시각이 open 이상이면 source 오류다.

### Start

- exact preregistered trial만 시작한다.
- `started_at`은 같은 뉴욕 거래일의 정규장 `[open, close)` 안이어야 한다.
- 이미 started면 최초 event를 exact replay한다.
- terminal trial을 다시 시작하지 않는다.

### Terminal

- `occurred_at`은 같은 뉴욕 거래일의 정규장 close 이후여야 한다.
- sequence 2이며 exact started event key를 parent로 사용한다.
- terminal 뒤 추가 event와 다른 terminal kind로의 재분류는 금지한다.

## 5. Completed와 Censored evidence

정상 장후 finalizer는 다음 source를 모두 다시 검증한다.

1. exact trial registration과 started event
2. global ORB strategy version registration
3. finalized flat `LaneDailySnapshot`
4. snapshot에 결합된 exact `LaneReviewEvent`
5. parent JSONL에 존재하는 exact `DailyResearchRecord`
6. review가 결합한 exact adaptive JSON bytes
7. daily record의 모든 artifact path·size·SHA-256 재계산과 `data_version` 재계산

record의 strategy version, scope, evaluator, feed, parameter, cost, portfolio와 code version은 preregistered trial·global strategy version과 정확히 일치해야 한다. 하나라도 다르면 terminal event를 만들지 않고 fail-closed한다.

terminal artifact는 정렬된 다음 네 SHA-256이다.

- daily record raw bytes
- adaptive evaluation raw bytes
- lane snapshot canonical key
- lane review event canonical key

`completed`는 daily `forward_day_eligible=true`, daily incidents 없음, snapshot `data_quality_complete=true`, snapshot incidents 없음일 때만 허용한다. 그 외 exact evidence가 모두 존재하는 세션은 `censored`이며 다음 고정 reason을 필요한 만큼 사용한다.

- `forward_day_ineligible`
- `daily_incidents_present`
- `snapshot_data_quality_incomplete`
- `snapshot_incidents_present`

검열은 수익 0이나 실패 거래로 바꾸지 않는다.

## 6. Failed evidence

장후 child process가 nonzero로 끝나면 이미 append된 phase audit CSV를 근거로 `failed` terminal을 만들 수 있다. 허용 phase는 닫힌 enum이다.

- `paper_metrics`
- `daily_research_record`
- `adaptive_evaluation`
- `lane_forward_validation`

failure service는 audit CSV의 schema, aware timestamp, 같은 뉴욕 거래일, nonzero exit code와 `failed` status를 검증하고 파일 SHA-256을 terminal artifact로 사용한다. reason은 `<phase>_phase_failed` 하나다. audit가 없거나 성공 행뿐이면 trial을 실패로 꾸미지 않고 열린 상태로 남겨 reconciliation 대상으로 둔다.

정규장 scan의 부분 provider 실패는 일일 record가 만들어지면 process failure가 아니라 `censored`로 분류한다.

## 7. CLI

local-only `run_orb_forward_trial.py`는 네 subcommand만 제공한다.

```text
register --experiment-ledger --lane-registry --session-date --output-dir
start --experiment-ledger --session-date --output-dir
finalize <session> --experiment-ledger --lane-registry --review-ledger --session-date --output-dir
fail --experiment-ledger --session-date --phase --audit --output-dir
```

서비스 테스트는 시계를 주입한다. 실제 CLI에는 `--registered-at`, `--started-at`, `--occurred-at`, fixture, force, credential, endpoint, arm 옵션을 노출하지 않는다.

보고서는 mode 600 atomic file이며 operation, completed/blocked, created/replayed, terminal kind와 broker mutation 0건만 남긴다. path, trial ID, strategy version, key, hash, raw reason, account·broker 식별자는 기록하지 않는다.

## 8. Watch 연결

기존 `run_kis_paper_watch.py`에 `--experiment-ledger`를 opt-in으로 추가한다.

- 이 옵션은 ORB와 기존 lane forward 네 경로가 모두 있을 때만 허용한다.
- watch가 장전 시작되면 provider 호출 전에 register한다.
- 장중 시작은 exact preregistration replay가 있을 때만 계속한다.
- 정규장 scan 전에 start한다.
- metrics→daily record→adaptive→snapshot→Reviewer 성공 뒤 finalize한다.
- 각 post-session child failure 뒤에는 해당 audit로 fail terminal을 시도한다.
- terminal projection 자체가 실패하면 기존 trial을 임의 failed로 바꾸지 않고 watch를 nonzero로 종료한다.

옵션이 없으면 기존 watch와 lane runner 동작은 바뀌지 않는다. trial Writer는 child CLI 프로세스가 짧게 lease를 소유하며 watch가 DB connection을 보유하지 않는다.

## 9. 권한과 비목표

- Alpaca/KIS network, credential, broker mutation, execution admission import 금지
- lifecycle state, champion, allocation과 주문권한 변경 금지
- `experimental_shadow → challenger`와 promotion 근거로 단독 사용 금지
- cross-lane 결과 혼합 금지
- open trial crash reconciliation 자동 추정 금지
- 기존 global experiment ledger schema 변경 금지
- fixed intraday Paper 위험 한도 변경 금지

## 10. 검증 기준

- preregistration timing, exact replay, code/scope/lineage mismatch fail-closed
- start regular-session timing과 restart replay
- completed/censored/failed exact terminal 및 terminal immutability
- daily artifact tamper, parent ledger mismatch, adaptive/snapshot/review mismatch 차단
- CLI help, unknown option, missing source, register/start/finalize/fail fixture QA
- watch의 register-before-provider, start-before-scan, ordered terminal/failure command
- Ruff, format, basedpyright와 전체 회귀
- 실제 Alpaca Paper POST/DELETE 0건

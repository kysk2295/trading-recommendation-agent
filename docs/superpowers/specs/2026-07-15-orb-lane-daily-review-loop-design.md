# ORB Lane Daily Snapshot And Reviewer Loop Design

날짜: 2026-07-15

상태: 승인된 lane 아키텍처의 다음 점진적 구현 경계

## 목적

`intraday_momentum`의 ORB Paper 전진검증 하루를 두 개의 독립 단계로 확정한다.

1. lane Writer가 장 종료 뒤 broker 상태와 shadow 연구 품질을 검증해 하나의 `LaneDailySnapshot`을 append한다.
2. 별도 Reviewer가 lane registry를 query-only로 읽고 exact-scope daily record와 adaptive evaluation을 검증해 전역 append-only review ledger에 권고를 남긴다.

이 결과는 확정 수익, 자동 승격, 위험 확대 또는 주문 승인 증거가 아니다. 실제 주문 endpoint는 추가하지 않으며 기존 Alpaca Paper GET/WSS readiness만 재사용한다.

## 선택한 접근

### 권장: Writer snapshot과 독립 Reviewer의 두 단계 원자 경계

- snapshot producer만 lane registry Writer lease를 획득한다.
- Reviewer는 lane registry에서 `LaneRegistryReader`만 사용하고 주문·execution Writer를 import하지 않는다.
- Reviewer 판정은 별도 global review ledger Writer에만 append한다.
- snapshot과 review는 각각 exact replay가 idempotent하고 같은 immutable identity의 내용이 달라지면 차단된다.

이 방식은 broker finalization 실패와 연구 판정 실패를 분리하고, Reviewer가 broker 상태를 만들거나 주문권한을 바꾸지 못하게 한다.

### 배제한 접근 1: readiness Markdown을 snapshot 근거로 파싱

사람용 보고서는 필드 추가나 문구 변경에 취약하고 원장 generation/hash를 증명하지 못하므로 사용하지 않는다.

### 배제한 접근 2: Reviewer가 broker와 execution DB를 직접 다시 읽기

독립 Reviewer가 자격증명, WSS 또는 execution Writer 경계에 접근하게 되어 역할 분리가 무너진다. Reviewer는 finalized snapshot과 checksum된 연구 산출물만 읽는다.

### 배제한 접근 3: snapshot 생성과 승격 상태 전이를 한 CLI에서 수행

짧은 표본이나 부분 실패가 주문권한 변경으로 이어질 수 있다. 이번 Reviewer event는 권고만 저장하며 `automatic_state_change_allowed=false`를 강제한다.

## 구성요소

### Execution ledger identity

`ExecutionStoreReader`에 query-only `ledger_snapshot_identity()`를 추가한다.

- current execution schema v9를 먼저 검증한다.
- 한 read transaction 안에서 모든 user table을 이름순으로 읽는다.
- 각 table의 schema, column, rowid와 scalar 값을 type tag와 길이 prefix로 정규 인코딩해 SHA-256을 계산한다.
- generation은 같은 transaction에서 읽은 모든 user table row 수의 합이다.
- BLOB은 메모리에 전체 ledger를 복사하지 않고 row 단위로 hash에 공급한다.
- path, account fingerprint, broker payload 또는 credential은 반환·출력하지 않는다.

동일 SQLite 내용은 WAL checkpoint 여부와 무관하게 같은 identity를 만들고, append가 발생하면 generation과 hash가 바뀐다.

### Intraday snapshot producer

새 service는 다음 입력만 받는다.

- initialized lane registry store
- current-schema intraday execution store
- ORB session directory와 session date
- 기존 `probe_paper_runtime()`이 만든 `PaperRuntimeReadiness`
- 호출자가 주입한 aware 현재시각

운영 CLI는 readiness fixture 옵션을 제공하지 않는다. 기본 경로는 mode 600 Paper credential과 고정 Paper URL을 사용하는 기존 GET/WSS probe다. 테스트는 Python dependency injection으로 fake readiness를 넣는다.

producer는 아래 순서로 fail-closed 검증한다.

1. session date가 지원되는 NYSE 거래일이다.
2. finalized time의 New York 날짜가 session date와 같고 official close 이후다.
3. market clock은 closed이고 account/clock/heartbeat 관측시각이 close 이후이며 기존 runtime freshness 대사가 통과했다.
4. broker open order와 nonzero position이 모두 0이다.
5. registry에 exact current intraday manifest, ORB experiment scope, 전용 account binding이 있다.
6. registry binding, execution DB binding, readiness account fingerprint가 모두 같다.
7. session의 최신 ORB daily record가 같은 date, scope, strategy/evaluator lineage를 가진다.
8. parent daily ledger에 exact record ID가 있고 다른 scope 표본은 사용하지 않는다.
9. execution ledger identity를 query-only transaction에서 확정한다.

성공하면 다음 값을 가진 `LaneDailySnapshot`을 append한다.

- `manifest_key`: current intraday manifest `1.0.1`
- `experiment_scope_keys`: exact ORB scope 하나
- `source_ledger_generation` / `source_ledger_sha256`: execution ledger identity
- `data_quality_complete`: daily record의 forward-day eligibility
- `incidents`: daily incident와 불완전 품질 blocker의 정렬·중복제거 결과
- `champion_strategy_versions`: 빈 tuple
- `allocation_eligible`: false
- `conservative_equity`: `min(account.equity, account.last_equity)`
- `realized_pnl`: flat account의 `equity - last_equity`
- `unrealized_pnl`, `planned_open_risk`, open order/position count: 0

같은 lane/date의 exact replay는 성공하되 새 행을 만들지 않는다. source hash, PnL, 품질 또는 incident가 달라진 재실행은 immutable conflict로 차단한다.

재실행 때 같은 lane/date snapshot이 이미 있으면 producer는 기존 `finalized_at`을 candidate에 재사용하고 나머지 broker·ledger·shadow 근거를 다시 계산한다. 따라서 단순 재관측 시각 변화는 exact replay를 깨지 않지만 source hash, PnL, 품질 또는 incident 변화는 숨겨지지 않는다.

### Global review ledger

별도 SQLite schema v1은 `lane_review_events` 한 table로 시작한다.

각 event는 다음 불변 계보를 가진다.

- lane/date, snapshot key, experiment scope key
- daily record ID와 raw file SHA-256
- adaptive evaluation raw file SHA-256
- strategy/evaluator/reviewer version
- adaptive action과 Reviewer action
- reasons와 blockers
- reviewed-at timestamp
- `automatic_state_change_allowed=false`
- `order_authority_change_allowed=false`

identity는 `(snapshot_key, experiment_scope_key, reviewer_version)`이다. exact replay는 false를 반환하고 다른 canonical payload는 typed conflict다. UPDATE/DELETE trigger, mode 600 DB, nonblocking single Writer lease와 query-only reader를 사용한다.

같은 identity의 기존 review가 있으면 Reviewer는 최초 `reviewed_at`을 candidate에 재사용하고 daily/adaptive hash와 판정 내용을 다시 계산한다. 근거가 같으면 replay이고 hash·action·blocker가 달라지면 conflict다.

review ledger는 account fingerprint, account number, API key, broker order ID, raw broker payload를 저장하지 않는다.

### Independent Reviewer

Reviewer CLI는 다음만 읽는다.

- `LaneRegistryReader`
- ORB session의 immutable daily record
- parent daily ledger
- `adaptive_evaluation/adaptive_evaluation.json`

다음을 검증한다.

1. lane/date snapshot이 정확히 하나이고 intraday flat invariant를 만족한다.
2. daily record date, ORB scope key, strategy version과 evaluator version이 일치한다.
3. parent daily ledger에 record ID가 있다.
4. adaptive evaluation의 `as_of`, strategy version, evaluator version이 daily record와 같다.
5. raw daily/adaptive file hash가 event에 고정된다.

adaptive action은 통계 권고로 보존하되 Reviewer action은 다음처럼 제한한다.

- `early_stop` 또는 `suspend`: `stop_recommended`
- `diagnose`: `diagnosis_required`
- `comparison_ready`: `comparison_ready`
- `promotion_review`: `promotion_review_blocked`
- 나머지: `continue_collection`

`promotion_review`는 snapshot에 champion이 없거나 allocation eligible이 false이면 반드시 blocker를 남긴다. 이번 producer는 둘 다 보수적으로 비활성화하므로 자동 champion 선언은 불가능하다. snapshot 품질 미완료, incident, daily/adaptive blocker도 정렬된 Reviewer blocker로 남긴다.

## 데이터 흐름

```text
Paper GET/WSS readiness + execution DB v9 + ORB daily record
    -> intraday snapshot producer (lane Writer)
    -> append-only lane_daily_snapshots

query-only lane registry + daily record + adaptive evaluation
    -> independent Reviewer
    -> append-only lane_review_events
```

앞 단계가 실패하면 뒤 단계는 실행하지 않는다. snapshot이 없으면 Reviewer event도 없다. Reviewer 실패는 이미 finalized된 snapshot을 수정하지 않는다.

## CLI와 보고서

### Snapshot

```bash
./run_intraday_lane_daily_snapshot.py \
  outputs/live_sessions/<session> \
  --session-date YYYY-MM-DD \
  --execution-database outputs/paper_execution/paper_execution.sqlite3 \
  --lane-registry outputs/lane_control/lane_registry.sqlite3 \
  --output-dir outputs/lane_control/snapshots/<date>
```

보고서는 ready/blocked, lane/date, flat counts, data quality, snapshot created/replayed 상태만 쓴다. fingerprint, path, key, credential, broker ID는 쓰지 않는다.

### Reviewer

```bash
./run_lane_reviewer.py \
  outputs/live_sessions/<session> \
  --session-date YYYY-MM-DD \
  --lane-registry outputs/lane_control/lane_registry.sqlite3 \
  --review-ledger outputs/lane_control/lane_review.sqlite3 \
  --output-dir outputs/lane_control/reviews/<date>
```

Reviewer 보고서는 권고·blocker·자동 상태변경 금지만 쓰고 raw hash/key/path는 노출하지 않는다.

## 오류 처리

- local schema, source lineage, market timing, account binding, broker flat, checksum 또는 identity 불일치는 mutation 전에 typed error로 차단한다.
- snapshot CLI는 local preflight가 끝나기 전 credential loader나 network probe를 호출하지 않는다.
- API/WSS 오류는 snapshot을 만들지 않고 redacted blocked 보고서를 남긴다.
- Reviewer는 credential을 로드하거나 network를 호출하지 않는다.
- report 쓰기 실패를 snapshot/review 성공으로 축소하지 않는다.
- append 뒤 report 실패가 발생해도 exact replay로 같은 immutable 결과를 다시 보고할 수 있다.

## 테스트

- execution identity의 안정성, append 변화, query-only schema 검증
- close 전, market open, stale evidence, nonflat broker, readiness failure 차단
- manifest/scope/account binding과 daily record lineage 불일치 차단
- flat fake readiness에서 snapshot append와 exact replay
- data-quality incomplete snapshot의 incident와 allocation false
- review ledger append-only, lease, idempotency, conflict, query-only
- Reviewer scope/date/version/hash 검증과 action mapping
- promotion review의 champion/allocation blocker
- snapshot CLI help, local failure before credential load, fake happy path redaction
- Reviewer executable help, malformed input, happy path, replay와 redaction
- full pytest, Ruff, changed-file format, basedpyright

## 운영 경계

- 실제 Alpaca POST/DELETE는 0건을 유지한다.
- 실제 GET/WSS smoke는 credential·Paper endpoint·시장시각 조건이 맞을 때만 별도 수동 QA로 수행한다.
- fake readiness E2E가 통과해도 실제 체결 품질이나 수익성을 증명하지 않는다.
- swing은 shadow-only, regime은 signal-only를 유지한다.
- Portfolio Manager와 자동 champion/promote/demote는 최소 두 lane champion 전까지 구현하지 않는다.

# ORB lane 독립 Reviewer loop 체크포인트

날짜: 2026-07-15

상태: **intraday final snapshot과 독립 Reviewer 권고 원장 연결 완료, champion·Portfolio Manager·자동 권한 변경 없음**

## 완성된 흐름

```text
Paper GET/WSS readiness + execution DB v9 + exact ORB daily record
→ intraday lane Writer
→ append-only LaneDailySnapshot

query-only LaneRegistryReader + exact daily record + adaptive JSON
→ independent Reviewer
→ separate append-only lane_review_events
```

snapshot과 review는 서로 다른 SQLite와 Writer lease를 사용한다. Reviewer 실패는 finalized snapshot을 수정하지 않고, snapshot 실패 시 Reviewer event도 만들지 않는다.

## Snapshot 경계

- local registry/execution/session preflight가 credential과 network보다 먼저다.
- 지원 NYSE 거래일의 official close 이후 같은 New York 날짜여야 한다.
- account·clock·market timestamp·WSS Pong·portfolio 관측은 평가시각 기준 5초 이내다.
- 시장 closed, runtime reconciliation ready, entry order·보호 OCO·nonzero position·portfolio exposure 0을 요구한다.
- registry, execution DB, readiness account fingerprint와 execution path fingerprint가 모두 같아야 한다.
- current intraday manifest `1.0.1`, exact ORB scope, parent daily ledger와 strategy/evaluator lineage를 다시 검증한다.
- execution source generation/hash는 query-only transaction의 canonical ledger identity다.
- 같은 근거의 replay는 최초 `finalized_at`을 재사용해 1행을 유지하고 PnL·hash·품질·incident 변경은 conflict다.

snapshot은 champion version을 비워 두고 allocation eligible을 false로 고정한다. data quality가 불완전하면 `data_quality_incomplete` incident를 남긴다. 이 record는 수익 확정이나 주문 승인 증거가 아니다.

## Reviewer 경계

- `LaneRegistryReader`는 `mode=ro`·`query_only`만 사용하고 Writer 메서드가 없다.
- Reviewer는 Alpaca client, credential loader, HTTP, execution store와 mutation 모듈을 import하지 않는다.
- stored snapshot key, ORB scope/date/manifest와 flat invariant를 payload에서 다시 계산해 확인한다.
- exact daily record raw SHA-256과 `adaptive_evaluation.json` raw SHA-256을 event에 고정한다.
- daily parent ledger, strategy/evaluator version과 adaptive as-of/date/version이 모두 같아야 한다.
- blocker는 snapshot quality/incident, daily promotion blocker, adaptive proof blocker, champion 부재와 allocation 비적격의 정렬 합집합이다.
- 같은 `(snapshot_key, scope_key, reviewer_version)`의 exact replay는 최초 `reviewed_at`을 재사용하고 1행을 유지한다. raw byte나 action 변경은 immutable conflict다.

adaptive action은 다음 Reviewer 권고로만 투영된다.

| adaptive action | Reviewer action |
|---|---|
| `collecting`, `shadow_continue` | `continue_collection` |
| `early_stop`, `suspend` | `stop_recommended` |
| `diagnose` | `diagnosis_required` |
| `comparison_ready` | `comparison_ready` |
| `promotion_review` | `promotion_review_blocked` |

`LaneReviewEvent`는 `automatic_state_change_allowed=false`와 `order_authority_change_allowed=false`만 허용한다. Pydantic `model_copy` 우회를 막기 위해 review Writer가 저장 직전에 전체 payload를 다시 검증한다.

## 별도 review ledger

schema v1의 `lane_review_events` 한 테이블로 시작한다.

- DB와 lock 파일 mode 600
- nonblocking single Writer lease
- UPDATE/DELETE 차단 trigger
- canonical event payload와 deterministic SHA-256 event key
- `(snapshot_key, experiment_scope_key, reviewer_version)` immutable identity
- independently constructible query-only reader
- stored payload와 event key, identity column 재검증

review ledger에는 account fingerprint, 계좌번호, credential, broker order ID 또는 raw broker payload를 저장하지 않는다.

## CLI

```bash
./run_intraday_lane_daily_snapshot.py outputs/live_sessions/<session> \
  --session-date YYYY-MM-DD \
  --execution-database outputs/paper_execution/paper_execution.sqlite3 \
  --lane-registry outputs/lane_control/lane_registry.sqlite3 \
  --output-dir outputs/lane_control/snapshots/<date>

./run_lane_reviewer.py outputs/live_sessions/<session> \
  --session-date YYYY-MM-DD \
  --lane-registry outputs/lane_control/lane_registry.sqlite3 \
  --review-ledger outputs/lane_control/lane_review.sqlite3 \
  --output-dir outputs/lane_control/reviews/<date>
```

Reviewer CLI는 완전 로컬이다. 보고서는 lane/date, adaptive·Reviewer action, allowlist된 blocker, created/replayed/conflict와 두 권한 금지만 쓰며 path, key, hash, account, credential, broker ID와 raw payload를 쓰지 않는다.

## 수동 QA와 검증

- snapshot CLI: help 0, invalid date 2, local missing source credential loader 0회, fake created/replayed 0, snapshot 1행
- Reviewer CLI: help 0, missing source 1, local happy 0, replay 0, adaptive raw tamper conflict 1, review event 1행
- Reviewer executable AST: Alpaca·credential·HTTP·execution store·mutation import 없음
- 마일스톤 표적 회귀: `92 passed`
- 전체 회귀: `758 passed`
- `uv run ruff check .`: 통과
- `uv run basedpyright`: `0 errors, 0 warnings`
- 변경 Python 파일 `ruff format --check`: 통과

이번 구현과 QA에서 Alpaca credential 또는 broker network를 사용하지 않았고 외부 Paper POST/DELETE는 0건이다. 기존 축소 위험은 notional 100 USD, 계획위험 10 USD, 최대 1포지션, 일손실 30 USD, 편도 20bp와 risk fraction 1/3000을 유지했다.

## 아직 없는 것

- Paper champion 선언 또는 자동 promote/demote
- 최소 두 champion 전 Portfolio Manager와 다음 세션 risk budget 배분
- swing broker 계좌·주문권한
- market regime 직접 거래권한
- 실제 정규장 entry→보호 OCO→Account Activities/WSS/REST→EOD flat 1건의 완결 smoke

연구 결과와 Reviewer 권고는 확정수익이 아니라 Paper forward-validation 후보와 blocker 기록이다.

# ORB intraday lane 일일 snapshot 체크포인트

날짜: 2026-07-15

상태: **장 종료 flat snapshot producer와 GET/WSS-only 운영 CLI 완료, 자동 승격·위험 확대 없음**

## 구현 경계

- `ExecutionStoreReader.ledger_snapshot_identity()`는 current schema v9를 query-only transaction으로 읽고 모든 user table의 schema·rowid·typed scalar를 canonical SHA-256으로 고정한다. generation은 같은 transaction의 전체 행 수다.
- exact ORB daily source는 날짜·전략·experiment scope가 같은 최신 record만 선택하고 parent `daily_research_ledger.jsonl`의 record ID 포함을 요구한다. schema v1 파일은 재작성하지 않고 역사적 intraday scope로만 투영한다.
- `LaneRegistryReader(path)`는 Writer 없이 독립 생성할 수 있고 `mode=ro`·`query_only`로 lane/date snapshot 한 건만 조회한다. `.writer()`는 `LaneRegistryStore`에만 남는다.
- producer는 현재 intraday manifest `1.0.1`, ORB scope `H-MOM-ORB-001`, 전용 account binding, execution path fingerprint와 exact daily lineage를 로컬 preflight에서 먼저 확인한다.
- readiness 뒤 같은 immutable source를 다시 읽어 중간 변경을 차단하고 성공할 때만 lane registry Writer lease로 `LaneDailySnapshot`을 append한다.

## 장 종료 안전 게이트

snapshot은 다음 조건이 모두 참일 때만 생성된다.

- session date가 게시된 NYSE 거래일이고 평가시각의 New York 날짜가 같으며 official close 이후다.
- account·market clock·market timestamp·WSS Pong·portfolio 관측이 close 이후이면서 평가시각 기준 5초 이내다.
- runtime readiness와 reconciliation이 ready이고 시장은 closed다.
- broker entry order·보호 OCO·nonzero position과 portfolio exposure가 모두 0이다.
- registry binding, execution DB binding, readiness account fingerprint가 정확히 같다.
- execution ledger path fingerprint, account binding 시각, exact ORB strategy/evaluator/scope 계보와 parent daily ledger가 모두 맞는다.

오류 문자열과 보고서는 account fingerprint, 경로, key, hash, credential, broker ID와 upstream 상세를 출력하지 않는다. local source가 불완전하면 credential loader와 network probe 전에 종료한다.

## Snapshot 의미

- manifest와 experiment scope는 현재 ORB intraday 계약 하나만 참조한다.
- source generation/hash는 query-only execution identity다.
- conservative equity는 `min(equity, last_equity)`, realized PnL은 flat account의 `equity - last_equity`다.
- open order·position·planned risk·unrealized PnL은 0이다.
- champion version은 비어 있고 allocation eligible은 항상 false다.
- daily quality가 불완전해도 snapshot은 보수적으로 확정할 수 있지만 `data_quality_incomplete` incident와 `data_quality_complete=false`를 남긴다.
- 같은 lane/date와 같은 근거의 재실행은 최초 `finalized_at`을 재사용해 exact replay하며 새 행을 만들지 않는다. execution hash, PnL, 품질 또는 incident가 달라지면 immutable conflict다.

## 운영 CLI

```bash
./run_intraday_lane_daily_snapshot.py outputs/live_sessions/<session> \
  --session-date YYYY-MM-DD \
  --execution-database outputs/paper_execution/paper_execution.sqlite3 \
  --lane-registry outputs/lane_control/lane_registry.sqlite3 \
  --output-dir outputs/lane_control/snapshots/<date>
```

운영 CLI는 fixture·force·arm 옵션이 없다. local preflight 뒤에만 mode 600 Paper credential을 읽고 기존 `probe_paper_runtime()`의 GET/WSS 경로를 사용한다. 보고서는 finalized/blocked, created/replayed, flat count, data quality, allocation 금지와 외부 mutation 0건만 기록한다.

## 수동 QA와 검증

- executable `--help`: 종료코드 0, fixture 우회 플래그 없음
- 잘못된 session date: argparse 종료코드 2, 외부 호출 없음
- missing registry/execution/session: credential loader 0회, snapshot 0건, generic blocked 보고서
- fake flat readiness: 첫 호출 created, 두 번째 replayed, registry snapshot 1건
- broker blocked readiness: snapshot 0건, upstream 상세 redaction
- producer·CLI 집중 회귀: `23 passed`
- 전체 회귀: `699 passed`
- `uv run ruff check .`: 통과
- `uv run basedpyright`: `0 errors, 0 warnings`
- 변경 Python 파일 `ruff format --check`: 통과

이번 체크포인트에서 실제 Alpaca credential이나 네트워크를 사용하지 않았고 Paper POST/DELETE는 0건이다. fake GET/WSS readiness와 로컬 원장 검증은 전략 수익성 또는 실제 체결 품질의 증거가 아니다.

## 다음 안전 단계

1. 독립 Reviewer event 계약과 별도 append-only review ledger를 구현한다.
2. Reviewer는 `LaneRegistryReader`, exact daily record, adaptive artifact만 읽고 credential·broker·execution Writer를 import하지 않는다.
3. promotion review를 권고로만 기록하고 champion·allocation·주문권한을 자동 변경하지 않는다.
4. 열린 정규장 smoke는 기존 축소 한도에서만 별도로 수행하며 시장이 닫혀 있으면 억지로 POST하지 않는다.

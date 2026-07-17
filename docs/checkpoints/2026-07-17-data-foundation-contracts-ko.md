# Data Foundation Contracts 체크포인트

- 날짜: 2026-07-17
- 범위: Institutional Multi-Market Quant Research OS Milestone 2 contract-only foundation
- provider network access: 0건
- credential loading: 0건
- broker mutation: 0건
- schema migration: 없음

## 구현

- `DataSourceId`는 provider/feed를 안정된 source identity로 분리한다.
- `DataEntitlement`는 허용 market·event·use, real-time/historical 권한, 유효기간, 재배포, raw/derived retention과 correction/deletion 계약을 보존한다.
- `DataCapability`는 source class, universe, delivery/timestamp semantics, historical depth, latency, rate/connection limits, freshness/completeness SLO와 assessed health를 보존한다.
- `StrategyDataRequirement`는 exact lane, primary source, 순서가 있는 명시적 fallback, 최소 품질과 실패 시 `blocked_by_data` 또는 `research_only`를 고정한다.
- 순수 data gate는 선언된 source만 평가하고 각 시도의 고정 실패 사유와 fallback 선택을 보존한다. hard requirement 실패는 soft research-only 요구보다 우선한다.
- `InstrumentId`와 유효기간이 있는 alias를 분리한다. alias 0건·중복 해석, 겹치는 point-in-time interval과 잘못된 corporate action shape는 추정하지 않고 차단한다.
- `CanonicalEventEnvelope`는 event/published/provider/received/normalized/effective timestamp를 대체하지 않고 독립 보존하며 correction·tombstone은 새 event로만 연결한다.
- `DataFoundationManifest`는 source↔entitlement↔requirement, instrument↔alias/action과 event↔source/entity/event-type 교차참조를 검증한다.
- offline `run_data_foundation_check.py`는 invalid contract 1, valid data block 2, ready 0을 구분하고 mode `600` aggregate report만 쓴다.

## 변경하지 않은 것

- 기존 US·KR collector, raw receipt와 SQLite ledger
- provider credential loader와 endpoint
- 전략 feature, 추천, backtest와 lifecycle
- Alpaca Paper entry·OCO·cancel·flatten 실행
- live capability registry 저장소, raw lake, Parquet/DuckDB replay

`examples/data/us-orb-data-foundation-v1.json`은 계약 검증용 fixture다. 실제 SIP entitlement, live provider health, 운영 coverage 또는 수익성 근거가 아니다.

## 검증

- security master contracts: `19 passed`
- capability/entitlement models와 pure gate: `44 passed`
- canonical event와 cross-contract manifest: `27 passed`
- CLI contract: `6 passed`
- 전체 회귀: `1774 passed`
- Ruff: `All checks passed!`
- basedpyright: `0 errors, 0 warnings, 0 notes`
- `git diff --check`: 종료코드 0
- 수동 CLI `--help`/missing/fixture ready: 종료코드 `0/1/0`
- direct CLI dependency resolution: offline
- 최종 보고서 권한: mode `600`
- provider network·credential·broker mutation: 0건

## 다음 경계

Milestone 3은 기존 bounded collector의 immutable raw receipt를 content-addressed object partition manifest로 투영하는 read-only 단계부터 시작한다. 기존 원장을 재작성하거나 처음부터 분산 저장소를 도입하지 않는다. 그 projection과 replay conformance가 통과한 뒤에만 canonical Parquet writer와 DuckDB 연구 query를 추가한다.

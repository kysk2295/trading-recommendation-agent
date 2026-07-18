# US Opportunity Scanner Projection 체크포인트

- 날짜: 2026-07-19
- 범위: 실제 KIS Opportunity에서 M4.2 broad-scanner 입력까지의 local projection
- 외부 network 호출: 0건
- credential/account/order 접근: 0건

## 구현

- KIS CLI에 `research-foundation-manifest`, `research-projection-store`, `research-canonical-root` 세 opt-in 경로를 추가했다. 모두 없으면 기존 경로를 유지하고 일부만 있으면 credential 로딩 전에 종료한다.
- causal `OpportunitySnapshot`의 exact JSON bytes를 mode-600 append-only SQLite에 먼저 저장한다. 동일 Opportunity ID의 다른 payload, UPDATE와 DELETE는 거부한다.
- data-foundation manifest에서 관측시점에 유효한 symbol/provider-symbol alias를 정확히 하나 요구한다. instrument는 US equity/ETF, USD, active 조건을 모두 만족해야 한다.
- candidate별 canonical event를 private immutable Parquet로 발행하고 DuckDB replay를 통과한 `ResearchInputIdentity(scope=us_equities.broad_scanner)`만 만든다.
- projection row는 Opportunity, dataset directory와 scanner snapshot을 함께 보존한다. latest reader는 Parquet mode, hash, schema, dataset ID, raw manifest와 snapshot identity를 다시 검증한다.
- exact retry는 raw receipt, canonical dataset과 projection을 각각 한 건으로 유지한다. projection 전 alias 실패도 raw Opportunity 증거는 남긴다.

## 검증

- focused projection/KIS contract: **14 passed**
- full repository: **2176 passed**
- Ruff: 통과
- basedpyright: 0 errors/warnings
- no-excuse: 신규 production module 4개 위반 0건
- CLI help: 신규 opt-in 세 경로 노출
- CLI partial configuration: 종료코드 2, credential/network 접근 전 차단
- library fixture QA: durable latest snapshot과 canonical replay identity 일치

## 남은 경계

checked-in `us-orb-data-foundation-v1.json`은 `FIXT` 한 종목 fixture다. 따라서 이 체크포인트는 실제 KIS 동적 후보 전체를 해석하거나 실시간 streaming을 제공한다고 주장하지 않는다. 다음 단계는 공식 read-only US security master 원문을 raw-first로 보존하고 point-in-time instrument/alias manifest를 생성하는 adapter다. 이 단계에도 계좌, 주문, Paper mutation 권한은 없다.

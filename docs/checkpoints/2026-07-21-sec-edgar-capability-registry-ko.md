# SEC EDGAR Capability Registry 체크포인트

## 범위

Milestone 5의 SEC submissions recent와 additional-history 원장을 canonical data capability registry에 연결했다. 이 경로는 이미 확정된 로컬 evidence만 읽으며 provider, 자격증명, 추천, 계좌와 주문 권한이 없다.

## Evidence 계약

- `SecEdgarStore.capability_evidence()`는 하나의 query-only SQLite snapshot에서 schema, raw hash, receipt, run, parent manifest, filing observation과 correction chain을 먼저 재검증한다.
- recent parent를 첫 slice로, 부모 raw receipt가 선언한 각 additional-history file을 후속 slice로 센다.
- child collection ID는 부모 receipt와 typed manifest에서 다시 결정한다. 저장된 child가 없으면 `missing`, terminal success면 `successful`, terminal failure면 `failed`다.
- filing 수와 가장 이른 실제 filing date는 검증된 성공 run의 observation만 사용한다. manifest의 `filingFrom`을 실제 수집 coverage로 대신하지 않는다.
- 최신 event receipt 시각은 filing observation이 있는 성공 run에서만 계산하고, terminal 완료시각은 별도 source heartbeat로 유지한다.
- failed recent는 manifest coverage를 추정하지 않고 `1` declared, `0` successful, `1` failed slice로 닫는다.

## Capability 계약

- source: `sec/edgar_submissions`
- class: `regulatory_fundamental`
- event type: `filing_metadata`
- universe: `us_equities:bounded_issuer`
- delivery: `rest_snapshot`
- timestamp semantics: `provider_time`, `received_at`
- local rate contract: `600 requests/minute`
- permitted uses: `historical_research`, `shadow_forward`
- redistribution: `derived_only`

한 parent collection은 한 issuer의 bounded assessment다. 이 결과를 미국 전체 상장사 coverage, always-on freshness 또는 Paper recommendation readiness로 해석하지 않는다.

## Health 계산

| Evidence | Health | Completeness |
|---|---|---|
| recent와 모든 declared history 성공 | `complete` | successful / declared |
| recent 성공, history 미수집 | `incomplete` | successful / declared |
| recent 성공, history terminal 실패 | `degraded` | successful / declared |
| recent terminal 실패 | `failed` | `0` |

`complete`만 10,000 bps를 만족한다. 실패나 미수집 slice는 결과가 0건인 성공 slice와 구분한다.

## 실행

```bash
uv run --script run_sec_edgar_capability_registry.py \
  --parent-collection-id sec-YYYYMMDD-001 \
  --cik 0000320193 \
  --database outputs/us_regulatory/sec_edgar.sqlite3 \
  --registry outputs/data_capability/registry.sqlite3 \
  --output-dir outputs/data_capability/sec-edgar-latest
```

완전한 assessment는 exit `0`, 유효하지만 incomplete/degraded/failed인 assessment는 mode-600 report를 남기고 exit `2`, 경로·원장·계약 오류는 민감값 없이 exit `2`의 CLI parameter error로 닫힌다. exact retry는 registry row를 늘리지 않는다.

## 검증

- SEC focused: `136 passed`
- full suite: `2989 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings`
- capability focused: complete, missing, failed child, failed recent, invalid evidence, registry append/replay와 path collision `19 passed`
- production SEC GET: `0`
- credential read: `0`
- broker/account/position/order operation: `0`

후속 filing document 본문 경계는 [SEC filing document raw-first 체크포인트](2026-07-21-sec-filing-document-raw-first-ko.md)에서 완료했다. 다음 M5 경계는 issuer/company-announcement evidence를 독립 source로 추가하는 것이다.

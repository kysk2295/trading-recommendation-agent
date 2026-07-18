# Alpaca SIP trade correction history 체크포인트

## 완성 범위

- 공식 stock stream의 `t` original, `c` correction, `x` cancel/error JSON shape를 strict하게 파싱한다.
- raw WebSocket frame bytes를 mode-600 single-writer SQLite에 먼저 append한다.
- stored receipt만 canonical `trade` dataset 입력으로 허용한다.
- `oi`, `ci`, `i` provider alias를 현재 active event에 연결하고 market date를 포함한 stable root provider identity를 유지한다.
- correction은 직전 active event를 대체하고 cancel/error는 chain을 tombstone으로 종결한다.
- private typed Parquet publish와 canonical history replay로 두 frame에 걸친 chain을 다시 검증한다.

## Complete-history 경계

- parser가 correction/tombstone을 지원하는 것과 provider 이력이 완전한 것은 다르다.
- fixture에는 WebSocket auth, trade subscription ACK, connection epoch와 disconnect evidence가 없다.
- coverage는 `raw_first_verified=true`, correction/tombstone support를 기록한다.
- `continuity_attested=false`이므로 `complete_history=false`와 `continuity_unattested`를 반환한다.
- complete history를 요구하는 evidence gate는 이 fixture 결과를 차단한다.

## 실패와 권한 경계

- missing original, original value mismatch, duplicate provider alias, tombstone 뒤 correction을 차단한다.
- symbol과 canonical instrument binding이 없으면 차단한다.
- 기존 REST minute-bar capability는 `snapshot_only`이며 trade correction collector로 표현하지 않는다.
- 실제 Alpaca WebSocket, credential, account/order endpoint와 broker mutation은 0건이다.
- 추천, strategy lifecycle, Paper execution 권한은 추가되지 않는다.

## 안전한 CLI

```bash
uv run python run_alpaca_sip_trade_history_fixture.py \
  --input fixtures/alpaca-sip-trade-history.json \
  --store outputs/alpaca-sip-trades/raw.sqlite3 \
  --output-root outputs/alpaca-sip-trades/canonical
```

CLI summary는 event/correction/tombstone/raw frame 수, dataset ID, active trade 수와 history coverage 사유만 출력하며 raw payload를 출력하지 않는다.

## 검증

- feature·store·Parquet replay·coverage·CLI targeted: 17 passed
- capability 관련 targeted: 69 passed
- full repository: 2343 passed
- Ruff: passed
- basedpyright: 0 errors, 0 warnings
- compileall, changed-file format, no-excuse rules: passed
- 실제 network request, credential load, account/order endpoint와 broker mutation: 0건

## 다음 단계

- read-only Alpaca market-data WebSocket endpoint 고정과 redirect/proxy 차단
- auth·trade subscription ACK의 raw-first control receipt
- 단일 connection epoch owner와 bounded continuity evidence
- disconnect/reconnect gap fail-closed 및 fixture recovery E2E
- 열린 정규장에서 credential·현재시점 조건이 자연스럽게 맞을 때만 bounded read-only smoke

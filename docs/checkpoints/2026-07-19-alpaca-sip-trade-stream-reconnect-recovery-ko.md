# Alpaca SIP trade stream reconnect recovery 체크포인트

## 목적

단일 WebSocket 연결이 끊겨도 이미 받은 raw trade frame을 잃지 않고, 새 연결을 독립 epoch로 보존한다. epoch 사이 공백은 provider backfill 증거 없이 complete history로 승격하지 않는다.

## 구현 계약

- 데이터 수신 뒤 timeout·disconnect가 발생한 session은 `failed` terminal로 닫힌다.
- failed session도 그 epoch에 append된 exact receipt ID 범위를 보존한다.
- 재시작 reader는 같은 market date와 symbol의 terminal session 전체를 한 read-only SQLite snapshot에서 읽는다.
- control wire hash, connected/auth/subscription 순서, terminal content hash와 data-link sequence를 epoch마다 다시 검증한다.
- 재연결은 새 connection epoch이며 기존 terminal이나 data-link를 덮어쓰지 않는다.
- 다중 epoch coverage는 receipt의 비중복 소유권, 수신시각 범위, market date와 provider event identity를 canonical batch와 대사한다.
- 여러 epoch의 합집합에 correction과 tombstone이 있어도 연결 공백의 별도 backfill 증거가 없으면 `continuity_unattested`다.

## 로컬 재현

```bash
uv run python run_alpaca_sip_trade_history_fixture.py \
  --input fixtures/alpaca-sip-trade-history-reconnect.json \
  --store outputs/alpaca-sip-reconnect/raw.sqlite3 \
  --stream-store outputs/alpaca-sip-reconnect/stream.sqlite3 \
  --simulate-reconnect-after 1 \
  --output-root outputs/alpaca-sip-reconnect/canonical
```

정상 결과는 session 2개, failed session 1개, control 6개, data link 2개, network request 0건이며 history는 incomplete다.

## 검증

- SIP targeted: 42 passed
- full repository: 2368 passed
- Ruff: passed
- basedpyright: 0 errors, 0 warnings
- CLI help: exit 0
- invalid reconnect split: exit 1, store·network 0
- reconnect fixture: exit 0, correction 1, tombstone 1, `continuity_unattested`
- account/order endpoint와 broker mutation: 0건

## 남은 운영 검증

- 열린 NYSE 정규장에서 bounded actual SIP frame 1개 수집
- 실제 disconnect가 자연 발생하면 epoch history와 canonical receipt 범위 대사
- pre-auth handshake·connection-limit/provider error의 sanitized terminal attempt evidence
- 장기 실행 전 명시적 bounded reconnect supervisor와 provider backfill 정책

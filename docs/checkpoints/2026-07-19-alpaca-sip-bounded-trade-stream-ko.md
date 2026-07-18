# Alpaca SIP bounded trade stream 체크포인트

## 완성 범위

- read-only stock stream endpoint를 exact `wss://stream.data.alpaca.markets/v2/sip`로 고정한다.
- proxy와 compression을 끄고 연결·ping·close timeout, frame 크기와 queue를 제한한다.
- connected, authenticated, subscription control frame을 raw SQLite에 먼저 쓴 뒤 strict하게 파싱한다.
- 요청 종목의 trade와 provider가 자동 포함하는 correction·cancel/error 구독만 exact ACK로 허용한다.
- 한 connection epoch의 data frame을 기존 trade raw store에 먼저 저장하고 receipt generation을 stream audit에 연결한다.
- clean bounded session은 마지막 검증 raw frame 수신시각까지의 attestation만 만든다.
- canonical batch의 date·symbol provider identity·exact raw receipt set·시간 범위가 attestation과 맞을 때만 complete history다.

## 감사 저장소

- control frame, data link, terminal session은 mode-600 current-user regular file에 append한다.
- writer lock도 mode 600, no-symlink, single-link 조건을 검사한다.
- 세 table 모두 update/delete trigger로 append-only다.
- schema version뿐 아니라 exact table·trigger object set을 read-back 때 확인한다.
- control payload SHA-256, exact control wire, sequence, terminal content hash와 data count를 다시 계산한다.
- zero-data, malformed frame, wrong symbol, timeout·disconnect·protocol failure는 `failed` terminal이거나 attestation 부재로 닫힌다.

## 안전한 fixture CLI

```bash
uv run python run_alpaca_sip_trade_history_fixture.py \
  --input fixtures/alpaca-sip-trade-history.json \
  --store outputs/alpaca-sip-trades/raw.sqlite3 \
  --stream-store outputs/alpaca-sip-trades/stream.sqlite3 \
  --output-root outputs/alpaca-sip-trades/canonical
```

이 경로는 실제 stream session API와 canonical projection을 통과하지만 local fixture connection과 고정 dummy credential만 사용한다. 자격증명 파일, network, account/order endpoint와 broker mutation은 열지 않는다.

## 검증

- stream/store/coverage/CLI targeted: 31 passed
- full repository: 2357 passed
- Ruff: passed
- basedpyright: 0 errors, 0 warnings
- changed-file format, compileall, no-excuse: passed
- 수동 CLI help 0, invalid input 1, bounded fixture happy path 0
- 실제 WebSocket request, credential file read, account/order endpoint와 mutation: 0건

## 남은 경계

- 열린 NYSE 정규장·private market-data credential·SIP entitlement가 동시에 맞을 때의 단일 종목 bounded read-only smoke
- disconnect/reconnect를 새 epoch로 분리하고 epoch 사이 gap을 complete history에서 차단하는 recovery cursor
- connection-limit·provider error terminal evidence와 장기 soak
- 전 종목 trade tape나 주문·추천·전략 승격 권한은 이 체크포인트 범위가 아니다.

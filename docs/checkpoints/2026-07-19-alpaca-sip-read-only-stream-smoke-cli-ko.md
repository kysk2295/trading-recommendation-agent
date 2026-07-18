# Alpaca SIP read-only stream smoke CLI 체크포인트

## 목적

bounded stream library를 실제 운영자가 안전하게 한 번 실행할 수 있는 단일 진입점으로 묶는다. 이 명령은 market-data read-only이며 Alpaca Paper 또는 live trading account/order endpoint를 import하거나 호출하지 않는다.

## 사전 게이트

- `--arm-read-only`가 명시되어야 한다.
- 현재 시각이 저장된 NYSE 캘린더의 정규장 안이어야 한다.
- symbol과 canonical instrument binding이 strict 계약을 통과해야 한다.
- credential file은 current user 소유 regular file, exact mode 600, no-symlink, single hard link여야 한다.
- state root는 current user 소유 directory, exact mode 700, no-symlink여야 한다.
- 이 순서는 arm·session·입력 검증 뒤에만 credential을 읽고 그 뒤에만 state와 network를 연다.

## bounded 실행

- exact Alpaca SIP endpoint 한 연결만 사용한다.
- `max_frames`는 1~10, frame timeout은 0초 초과 10초 이하이다.
- 매 frame 수신 뒤 current regular session과 NY market date를 다시 검사한다.
- session/date가 바뀌면 raw frame은 보존하지만 terminal은 failed이며 report를 만들지 않는다.
- 성공 session은 exact epoch control/data receipt, canonical projection, complete coverage와 typed Parquet publication을 모두 요구한다.
- JSON summary와 private report에는 dataset/count/date/symbol만 있고 credential·raw payload·receipt ID·path는 없다.

## 실행 절차

```bash
uv run python run_alpaca_sip_trade_stream_smoke.py \
  --instrument-id us-equity-aapl \
  --symbol AAPL \
  --state-dir outputs/alpaca-sip-trade-stream-smoke \
  --max-frames 1 \
  --receive-timeout-seconds 5 \
  --arm-read-only
```

휴장, calendar 불일치, credential 또는 SIP entitlement 부족 상태에서는 반복 실행으로 우회하지 않는다.

## 검증

- SIP stream/history/CLI targeted: 38 passed
- full repository: 2364 passed
- Ruff: passed
- basedpyright: 0 errors, 0 warnings
- compileall, format, no-excuse: passed
- CLI help: exit 0
- arm 누락: exit 1, credential/network/state 0
- 2026-07-19 일요일 actual clock: exit 1, credential/network/state 0
- fixture happy: exit 0, control 3, data link 1, event 3, complete history, mode-700 root/mode-600 report
- fixture mid-session close: exit 2, failed terminal, report 0
- 실제 WebSocket connection, account/order endpoint와 broker mutation: 0건

## 다음 단계

- 열린 NYSE 정규장에서 frame 1개 actual read-only smoke
- actual control/data/terminal/canonical/report exact 대사
- disconnect/reconnect를 별도 epoch로 기록하고 사이 gap을 complete history에서 차단하는 recovery cursor
- connection-limit/provider error를 pre-auth terminal attempt evidence로 보존

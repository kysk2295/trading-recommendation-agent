# US Swing Shadow Vertical 체크포인트

날짜: `2026-07-16 KST`

## 완료 범위

- `us_equities/swing_trading/new_high_momentum`의 첫 실행 가능한 연구 vertical을 추가했다.
- 입력은 정규장 마감 뒤 확정된 미국 일봉이며, fixture 또는 current NYSE post-close의 bounded Alpaca market-data GET만 허용한다.
- v1 조건은 최근 20개 완료 세션보다 높은 종가와 평균 대비 `1.5x` 이상 거래량이다. 신호는 다음 정규 세션의 `stop_trigger` 조건부 추천이며 현재 호가 검증이나 주문이 아니다.
- 진입 trigger는 종가의 `50bp` 위, 손절은 trigger의 `800bp` 아래, 목표는 `2R`, 최대 보유는 진입 뒤 10개 완료 세션이다.
- dedicated SQLite ledger는 `signal_created`, `entry_filled`, `stopped`, `targeted`, `time_exit`, `expired` event를 append-only로 기록한다. stop/target이 같은 일봉에서 함께 충족되면 손절을 먼저 기록한다.
- ledger와 writer lock은 owner-only mode `600`이며 reader는 SQLite `query_only`다. 동일 ID의 다른 signal/event payload는 immutable conflict로 닫힌다.
- `run_us_swing_shadow.py` fixture E2E는 private JSONL, Korean signal card, aggregate report를 mode `600`으로 만들고 replay 시 신규 event나 publication을 만들지 않는다.

## 안전 경계

- Alpaca Paper endpoint, 계좌, 주문, 포지션, broker mutation import와 호출: 0건
- 실제 자금 거래: 0건
- production source는 current New York session, official regular close 이후, 1~50개의 정렬·중복 없는 universe를 먼저 확인한다. historical, pre-close, holiday 요청은 credential loader와 HTTP client 전에 거절한다.
- committed fixture 및 자동 검증은 network·credential·외부 메시지를 열지 않는다.
- report와 terminal에는 source hash, fixture path, database path, credential 또는 provider 상세를 쓰지 않는다.

## 검증

- focused source·signal·shadow·CLI suite: `31 passed`
- 전체 pytest: `1628 passed`
- Ruff: `uv run ruff check .` 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- 수동 CLI QA: `--help`는 session date, universe file, fixture root, database, output directory, secret path와 help만 노출했다.
- 수동 CLI QA: 잘못된 날짜는 exit 2로 source·database·output 생성 전에 차단됐다.
- 수동 CLI QA: committed fixture 첫 실행은 conditional signal 1건, shadow event 1건을 기록했고 exact replay는 신규 signal/event 0건이었다.
- 수동 CLI QA: database, writer lock, JSONL, report, signal card 모두 mode `600`이었다.

## 아직 하지 않은 일

- 실제 current-session Alpaca daily-bar read-only smoke: 0건
- multi-day production shadow forward 표본: 0건
- global experiment ledger trial, independent Reviewer, lifecycle promotion/demotion 연결: 0건
- `swing_momentum` Paper 계좌 binding 또는 Paper 주문: 0건

## 다음 단계

1. 현재 NYSE post-close에서만 bounded read-only production source를 한 번 수집한다.
2. 동일 source lineage를 preregistered global experiment trial과 Reviewer evidence에 별도 계약으로 연결한다.
3. 충분한 shadow forward 표본과 independent review 없이는 Paper 권한·risk limit·lifecycle을 변경하지 않는다.

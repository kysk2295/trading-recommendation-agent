# Alpaca SIP Runtime Provider Bridge 체크포인트

- 날짜: 2026-07-18
- 범위: M4.3 actual provider bridge fixture 검증
- 실제 외부 network 호출: 0건
- credential/account/order 접근: 0건

## 구현

- `AlpacaSipMinutePageClient`는 canonical `https://data.alpaca.markets/v2/stocks/bars`의 GET만 사용한다. `1Min`, `feed=sip`, `adjustment=raw`, ascending pagination을 고정하며 redirect와 다른 base URL은 요청 전에 차단한다.
- 한 adapter instance는 context에 고정된 exact instrument ID·symbol과 한 NYSE 세션만 처리한다. 종목 교체, 휴장·정규장 밖·다중 desired subscription·quote/trade 계약 불일치는 HTTP 전에 닫힌다.
- 현재 분은 제외하고 완료된 정규장 1분봉만 세션 open 기준 연속 sequence로 정규화한다. supervisor checkpoint 이후 sequence만 다시 전달하므로 정상 재시작은 증분 처리한다.
- HTTP page별 response body bytes를 별도 mode-600 append-only SQLite에 먼저 저장한다. UPDATE와 DELETE trigger를 금지하고 pagination page와 projection을 독립 계보로 남긴다.
- raw receipt manifest에서 canonical minute-bar Parquet를 발행하고 DuckDB exact replay를 통과한 `ResearchInputIdentity`만 runtime batch에 결합한다.
- 같은 분의 exact retry는 기존 raw page와 verified projection을 재사용해 `no_new_data`로 끝난다. provider minute 누락은 raw·canonical evidence 보존 뒤 supervisor의 `blocked_sequence_gap` incident가 된다. 이후 full-session 응답이 sequence 1부터 완전히 연속일 때만 verified identity에 결합된 새 recovery epoch를 열고, 누락이 남아 있으면 기존 gap checkpoint를 유지한다.
- 이 bridge는 완료 분봉 polling이며 websocket quote/trade streaming, 전체시장 coverage, 계좌 조회 또는 주문 실행이 아니다.

## Fixture E2E

- 두 HTTP page의 20+15개 SIP 분봉이 exact body 보존과 canonical replay를 거쳐 feature `ready`가 됐다.
- 20개 분봉 처리 뒤 새 process adapter가 같은 runtime store에서 21~35 sequence만 append해 `ready`로 복구했다.
- 같은 완료 분의 exact retry는 raw page 1개·projection 1개를 유지하고 runtime `no_new_data`로 종료했다.
- sequence 1·3만 온 응답은 두 receipt를 먼저 보존한 뒤 `blocked_sequence_gap`으로 닫혔다.
- 같은 process의 다음 응답이 sequence 1·2·3 전체를 반환하면 새 epoch에 세 receipt를 보존하고 `sequence_gap`·`reconnect` incident 순서로 clean runtime을 복구했다.
- 휴장, 다중 종목, noncanonical base URL과 redirect는 실제 HTTP 또는 후속 redirect 없이 sanitized error로 종료했다.

## 검증

- focused provider bridge와 supervisor: **19 passed**
- M4/raw/canonical 관련 회귀: **186 passed**
- full repository: **2170 passed**
- Ruff: 통과
- basedpyright: 0 errors/warnings
- compileall: 통과
- no-excuse: 변경 production module 모두 250 pure LOC 이하

## 다음 운영 단계

현재 날짜는 토요일이므로 실제 시장 GET을 억지로 실행하지 않았다. 다음 열린 NYSE 정규장에서 mode-600 market-data credential과 SIP entitlement가 확인될 때 단일 종목 bounded read-only smoke를 수행하고, raw page·canonical replay·runtime checkpoint를 대사한다. 이 단계에서도 Alpaca Paper credential, account, position, order endpoint와 broker mutation은 열지 않는다.

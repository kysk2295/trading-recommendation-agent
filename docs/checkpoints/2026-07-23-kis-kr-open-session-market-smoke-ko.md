# KIS KR 실제 장중 시장 데이터 smoke 체크포인트

## 실행 범위

2026-07-23 10:32~10:39 KST의 열린 KRX 세션에서 production
`run_kis_kr_market_collect.py`를 읽기 전용으로 실행했다. 호출 범위는 공식 KIS 국내
당일 분봉, 현재가 상태, 호가 예상체결 GET 세 종류뿐이다. 계좌, 잔고, 포지션, 주문
endpoint와 mutation은 호출하지 않았다.

첫 cycle은 당일 KIS ranking의 rank-1 종목을 사용했다. 분봉, 현재가, 호가 응답은 모두
HTTP 200이었고 mode-600 append-only receipt store에 raw bytes가 먼저 보존됐지만 CLI는
호가 envelope parsing에서 fail-closed했다.

## 실응답에서 발견한 계약 오류

공식 KIS sample commit `885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc`와 오늘의
실응답은 시장 구분 코드 `new_mkop_cls_code`를 호가 `output1`에 둔다. 기존 local
Pydantic boundary와 projection은 이를 예상체결 `output2`에서 요구하고 있었다.

- provider-shaped fixture로 기존 parser가 실패하는 RED를 재현했다.
- strict field ownership을 `KisKrOrderBookRow`로 옮겼다.
- projection은 `output1.new_mkop_cls_code`만 사용한다.
- 잘못된 과거 위치를 optional fallback으로 허용하지 않는다.

저장된 첫 cycle raw receipts를 수정 코드로 다시 읽었을 때 세 envelope는 모두 parsed
됐다. 다만 해당 rank-1 응답의 상·하한가가 0이어서 최종 risk snapshot은 계속
fail-closed했다. 이를 임의 ±30% 계산으로 보정하거나 추천으로 승격하지 않았다.

## 실제 재검증

같은 열린 세션에서 대표 보통주 `005930`으로 production CLI를 한 번 더 실행했다.

- CLI exit: `0`
- provider mode: `production`
- receipt 신규/재사용: `3/0`
- raw receipt kinds: minute bars, price/status, order book
- actual raw snapshot replay: valid
- session/trading mode projection: `open` / `continuous`
- receipt store와 redacted report mode: `600`
- account/order authority: `false`
- external mutation: `0`

private raw evidence는
`outputs/kr_theme/m7_market_smoke/2026-07-23/market_receipts-005930.sqlite3`에
보존했다. 이 파일은 저장소에 커밋하지 않으며 payload, credential과 계좌 식별자는
문서나 콘솔에 출력하지 않았다.

## 검증과 해석

- market adapter/downstream focused tests: `48 passed`
- full pytest: `3368 passed`
- Ruff: pass
- basedpyright: `0 errors, 0 warnings, 0 notes`
- provider-shaped raw replay: `open` / `continuous`
- production GET: 첫 cycle 3건 + 재검증 cycle 3건
- 국내 broker mutation: `0`
- Alpaca Paper POST/PATCH/DELETE: `0`

이 체크포인트는 M7의 실제 열린 세션 read-only market adapter 근거다. rank-1 종목이
완전한 risk snapshot을 만들지 못한 날에는 후보를 바꾸거나 차단 상태로 남겨야 한다.
전략 수익성, champion, 국내 주문 권한 또는 Allocation Manager 근거는 아니다.

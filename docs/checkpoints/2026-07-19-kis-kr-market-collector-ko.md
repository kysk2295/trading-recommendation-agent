# KIS KR 장중 GET-only collector 체크포인트

## 실행 권위

`run_kis_kr_market_collect.py`는 지정한 official KIS calendar snapshot에서 현재 KST 날짜가 business/trading/open day인지 확인하고 09:01 이상 15:30 미만일 때만 production credential을 읽는다. 수집 kernel도 세 GET 각각 직전에 같은 session date/time을 다시 확인한다.

production adapter는 기존 exact live origin, no redirect 정책 아래 다음 GET만 사용한다.

- 당일 완료 1분봉 `FHKST03010200`
- 현재가·VI·정지·지정 상태 `FHKST01010100`
- 호가·예상체결·장 상태 `FHKST01010200`

국내 account, balance, position, order endpoint와 mutation은 없다.

`--eod-minute`는 같은 official open day의 KST 15:30 이상 15:31 미만에만 15:29 마지막 완료 분봉 GET 하나를 허용한다. 이 phase에서는 현재가와 호가를 요청하지 않으며 응답에 exact 15:29 row가 없으면 raw 보존 뒤 차단한다.

## raw-first 부분 실패

각 provider response는 status/body parsing보다 먼저 `KisKrMarketReceiptStore`에 append한다. 이후 kind/symbol/time binding, HTTP 200, provider `rt_cd=0` 또는 payload shape 검증이 실패해도 이미 받은 raw bytes를 삭제하지 않는다. exact 재시작은 같은 logical receipt를 재사용하고 다른 payload는 conflict다.

strict fixture manifest는 symbol, 한 requested time과 세 kind의 received time·relative payload path를 고정한다. absolute path, symlink, fixture root 탈출, kind 누락·중복과 request mismatch를 차단한다. fixture는 credential과 network를 열지 않는다.

## 검증

- focused collector/kernel/CLI: `7 passed`
- related KR market/intraday: `26 passed`
- 전체 회귀: `2733 passed`
- Ruff 전체와 changed-file format: 통과
- basedpyright: `0 errors, 0 warnings`
- compileall, 신규 production no-excuse: 통과
- actual CLI help, fixture happy/replay `3/0 → 0/3`: 통과
- 2026-07-19 일요일 production block: credential call `0`, receipt store `0`, network `0`
- 국내 account/order mutation: `0`

## 다음 단계

재시작 가능한 KR day session manifest가 exact trial, calendar snapshot, Opportunity, receipt/entry/exit store와 phase audit을 결합한다. supervisor는 pre-open register/start, 장중 collector→entry child→exit projection, post-session control runner를 별도 child process로 직렬 실행하고 source store의 canonical replay로 마지막 미완료 phase만 계속한다.

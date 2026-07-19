# KR intraday market gate 체크포인트

## 목적

KR Theme Opportunity을 day shadow 신호로 바꾸기 전에 한국장 고유 체결 제약을 point-in-time evidence로 확인하고, 하나라도 불명확하면 fail-closed한다.

## 구현

- frozen `KrMarketConstraintSnapshot`이 종목, 관측시각, 전일종가·현재가·상하한가, bid/ask와 canonical evidence reference를 보존한다.
- 세션은 open/closed/unknown, VI는 clear/static/dynamic/unknown, 체결 방식은 continuous/call-auction/unknown으로 구분한다.
- 거래정지와 투자주의·경고·위험 지정도 clear/active/unknown을 구분한다.
- shadow entry gate는 5초 freshness, future evidence, +27% 상한가 근접, 상·하한가, 호가 결손과 crossed quote를 deterministic reason으로 차단한다.
- 모든 상태가 관측된 clear 상태일 때만 `eligible`이다. unknown을 정상으로 추정하지 않는다.
- 입력은 frozen이며 gate가 canonical model을 재검증하므로 unchecked `model_copy` 변경도 우회하지 못한다.

## 검증

- 14개 focused 회귀와 전체 2592 tests 통과
- Ruff, changed-file format, basedpyright 0/0, compileall, no-excuse 통과
- 최소 라이브러리 드라이버에서 clear=`eligible`, VI unknown=`blocked/vi_unknown` 확인

## 현재 경계

이 체크포인트는 provider-neutral gate 계약만 구현한다. LS/KIS 체결·호가·VI·단일가·거래정지·투자지정 read-only adapter, KRX 달력, KR 분봉 indicator, theme leader TradeSignal, shadow fill과 outcome은 아직 연결하지 않았다. 국내 계좌·잔고·포지션·주문 endpoint, 외부 network와 broker mutation은 0건이다.

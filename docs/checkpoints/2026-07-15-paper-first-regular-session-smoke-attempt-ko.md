# 첫 정규장 Alpaca Paper smoke 시도 체크포인트

실행 시각: 2026-07-15 09:44~09:52 EDT
판정: **SAFE SKIP / STAGE 1 BLOCKED**

## 결과

`feature/paper-account-activities`의 remote 최신 `00487f2`에서 첫 정규장 단계 1 smoke를 실행했다. 사용자 지정 기준 `7b033f3`은 이 commit의 ancestor다.

실제 Paper bootstrap, preflight, readiness와 최종 recovery를 순차 실행했다. broker clock과 로컬 NYSE calendar는 모두 정규장 open으로 일치했고, exact Paper WSS 인증·구독·Pong과 같은 세대의 REST·원장·포트폴리오 대사가 통과했다. 계좌는 시작과 종료 모두 open order 0, position 0이었다. 기존 account fingerprint binding은 exact 일치했으며 fingerprint 값은 보고서에 기록하지 않았다.

canonical execution ledger는 기존 schema v3에서 v9로 migration됐다. migration과 최종 대사 뒤 `PRAGMA quick_check`는 `ok`, order intent·mutation intent/event·보호 OCO 계획·safety action·FILL activity는 모두 0건이었다.

production `run_alpaca_paper_entry_smoke.py`를 exact arm으로 한 번 실행했지만 current watch SQLite에 30초 이내 causal ORB setup 후보가 0건이었다. source loader가 credential·session·provider 호출 전에 `InvalidCurrentOrbPaperEntrySourceError`로 닫혔다. 과거 후보나 임의 종목·가격·수량으로 우회하지 않았다.

따라서 entry, 보호 OCO, exact-ID cancel, exact-quantity flatten mutation은 모두 0건이다. 마지막 GET-only recovery와 preflight도 주문·FILL·OCO snapshot 0, open order 0, position 0, unresolved mutation 0을 확인했다. 최종 flat은 PASS지만 실제 최소수량 smoke 자체는 PASS가 아니다.

## 검증

- 전체 회귀: 946 passed
- Ruff: PASS
- basedpyright: 0 errors, 0 warnings
- CLI help: entry·protective OCO·safety mutation PASS
- 실제 Paper bootstrap·preflight·readiness·recovery: PASS
- broker mutation: POST/PATCH/DELETE 0건
- 전체 실행 최대 RSS: 115,359,744 bytes
- broker CLI 최대 RSS: 83,329,024 bytes
- swap: 0
- 운영 보고서: mode 600

## 다음 단계

다음 열린 정규장에서 exact current ORB setup 후보가 1건일 때 단계 1을 동일한 단발 arm으로 다시 시도한다. 실제 entry ACK·체결, 부분체결부터 exact 보호 OCO, exact-ID cancel, exact-quantity flatten, 최종 flat·unresolved 0·quick_check ok가 모두 확인되기 전에는 ORB 반복 pilot을 시작하지 않는다.

이 결과는 Paper 실행 안전성 증거이며 수익성이나 전략 승격 근거가 아니다.

# US Day Paper readiness Hermes 상태 체크포인트

날짜: 2026-07-22

## 제품 동작

- 기존 Alpaca Paper GET/WSS readiness CLI에 opt-in `--delivery-database`를 추가했다.
- readiness는 계좌 식별자, credential, stream epoch 또는 broker payload 없이 US Day lane 상태로 축약된다.
- flat·reconciled premarket은 Hermes `DAILY_SUMMARY`의 `waiting_regular_session`으로 투영된다.
- 정규장 ready, 시장 종료, runtime 불완전, 기존 exposure, broker clock 불일치도 각각 구조화된 상태를 갖는다.
- blocked 상태는 `INCIDENT`, 정상 비주문 상태는 `DAILY_SUMMARY`이며 어떤 상태도 주문을 승인하지 않는다.
- session date와 상태가 같은 재실행은 최초 heartbeat 시각을 재사용해 event 한 건으로 exact replay된다.
- 실행 원장이 없으면 credential, network와 delivery database를 열기 전에 차단된다.

## 실제 premarket read-only QA

- 실제 Alpaca Paper preflight: exit 0, 미체결 주문 0, 열린 포지션 0
- 실제 Paper readiness: WSS 인증·구독·Pong, REST·원장·포트폴리오 대사 통과
- broker market open: 아니오
- 격리 delivery DB 첫 실행/재실행: exit `0/0`, event 1건
- event kind/status: `daily_summary` / `waiting_regular_session`
- Paper POST/DELETE와 broker mutation: 0

## 검증

- readiness/Paper runtime/Hermes 집중 회귀: `32 passed`
- 전체 pytest: `3286 passed`
- Ruff 전체: 통과
- basedpyright 전체: `0 errors, 0 warnings, 0 notes`
- changed production format, compileall, no-excuse: 통과
- production 모듈: 각각 156, 128 pure LOC

## 남은 실제 증거

- 이 문서의 최초 commit 시점에는 격리 DB만 사용했으며 production Telegram message는 만들지 않았다.
- pushed code로 production delivery store에 오늘 premarket status를 한 번 투영한 뒤 acknowledgement와 replay 불변을 추가 기록한다.
- 이 readiness 상태는 US Day 추천이나 Paper lifecycle 완료 증거가 아니다. 정규장 자연 setup과 OCO/EOD 대사는 별도 M2 gate다.

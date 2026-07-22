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

## 실제 production Telegram 증거

- pushed commit `5e4e9b5`로 실제 Paper GET/WSS readiness를 다시 실행했다.
- production delivery store에 US `daily_summary / waiting_regular_session` event가 정확히 1건 생성됐다.
- 첫 attempt는 `telegram_timeout`으로 retry가 예약됐고 두 번째 attempt가 Telegram acknowledgement를 기록했다.
- 전체 원장 수치는 event `4`, attempt `9`, acknowledgement `2`, dead letter `2`가 됐다.
- 동일 readiness CLI replay와 3초 대기 뒤에도 위 수치와 target event 1건이 변하지 않았다.
- timeout 뒤 ACK이므로 delivery는 at-least-once다. 첫 timeout attempt가 플랫폼에서 수락됐는지는 로컬 원장만으로
  증명할 수 없어 Telegram 중복 가능성을 0이라고 주장하지 않는다.
- 자격증명, 계좌 fingerprint, chat/message ID는 출력하거나 문서화하지 않았다.
- production 실행과 replay 모두 Paper POST/DELETE 및 broker mutation은 0건이었다.

## 남은 제품 증거

- 이 readiness 상태는 US Day 추천이나 Paper lifecycle 완료 증거가 아니다.
- 정규장 자연 setup의 actionable card, armed Paper entry, 보호 OCO, EOD flat과 결과 ACK은 별도 M2 gate다.
- M1도 실제 US 추천 또는 정규장 무추천 결과가 전달될 때까지 완료로 올리지 않는다.

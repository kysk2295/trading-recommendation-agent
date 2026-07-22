# US ORB pre-open trial 사전등록 체크포인트

날짜: 2026-07-22

## 판정

- NYSE premarket에 오늘 ORB `shadow_forward` trial이 없음을 query-only로 확인했다.
- shared checkout은 다른 세션의 미커밋 변경이 있어 production 등록에 사용하지 않았다.
- `origin/main`의 pushed commit `d0a9b74`를 clean detached runtime에서 bootstrap했다.
- 이 새 code version의 lifecycle effective session은 anti-lookahead 규칙에 따라 2026-07-23이다.
- 따라서 `d0a9b74`로 2026-07-22 trial을 등록하려는 시도는 정상적으로 fail-closed했고 trial은 생성되지 않았다.
- 2026-07-22에 실제 유효한 사전등록 code version `be0cde7`의 clean runtime으로 다시 실행해 오늘 trial 1건을 등록했다.

## 인과성 계약

- 새 code version은 결정일 다음 적격 NYSE session부터만 연구 lifecycle에 효력이 생긴다.
- 오늘 개발한 코드를 오늘 결과에 소급 결합하지 않는다.
- trial strategy version은 어제 등록돼 오늘 effective인 ORB code-coupled version과 정확히 일치한다.
- planned start/end는 2026-07-22 단일 session이고 등록은 정규장 open 전에 완료됐다.
- trial event는 아직 0건이며 정규장 안에서 같은 effective runtime이 시작해야 한다.

## 재실행 검증

- 최초 effective registration: exit 0, planned trial 1건, event 0건
- 같은 clean runtime replay: exit 0, planned trial 계속 1건, event 0건
- replay 전후 experiment ledger mtime 불변
- credential, market-data provider, account, position, order endpoint 접근 0
- broker mutation 0

## 현재 운영 준비상태

- 실제 Alpaca Paper preflight/readiness는 WSS·REST·원장 대사 통과, 주문 0, 포지션 0이다.
- Hermes에는 premarket `waiting_regular_session` status가 전달되고 Telegram ACK가 기록됐다.
- 정규장 watch/start process는 이 체크포인트에서 실행하지 않았다.
- 명시적 one-use Paper arm도 생성·확인하지 않았으므로 Paper POST 권한은 계속 닫혀 있다.

## 다음 세션 단계

1. 정규장 open 시 `be0cde7` clean runtime으로 preregistered trial을 start한다.
2. 기존 ORB watch를 read-only로 실행해 current completed-minute 후보를 만든다.
3. 자연 setup이 없으면 threshold를 변경하지 않고 장후 censored no-setup으로 확정한다.
4. Paper entry는 별도의 명시적 owner arm이 확인된 경우에만 허용한다.

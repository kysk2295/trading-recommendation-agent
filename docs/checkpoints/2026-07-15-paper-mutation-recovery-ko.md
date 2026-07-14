# Alpaca Paper mutation 원장·current-epoch 복구 체크포인트

## 목적

ORB Paper pilot을 60거래일 동안 기다렸다가 시작하는 대신 안전 게이트 직후 소규모로 열려면, 보호 OCO·취소·평탄화 요청이 timeout 뒤 중복 전송되지 않아야 한다. 60거래일·100건은 수익 확정과 위험 확대의 최종 검토 문턱이고, pilot 운용 중에는 5/10/20/60 적격일·거래 롤링과 가격·갭·volume/ADV·거래대금 cohort로 매일 조기 중단한다.

## 구현 계약

- exact `https://paper-api.alpaca.markets`만 받는 별도 mutation adapter가 DAY OCO POST, broker order ID DELETE, exact integer quantity position DELETE의 요청·응답을 엄격 파싱한다.
- canonical method·path·query·body SHA-256과 source plan identity를 schema v7 append-only intent로 저장한다.
- broker 호출 전에 `ATTEMPTED`를 commit한다. 성공은 request ID·HTTP 상태·broker order ID를 가진 `ACKNOWLEDGED`, 명시적 HTTP 거부는 `REJECTED`, transport timeout과 불완전 응답은 `AMBIGUOUS`로 저장한다.
- ACK·거부·모호 상태는 같은 요청을 다시 전송하지 않는다. `RECOVERED_ABSENT`만 다음 attempt를 허용한다.
- 단일 Writer/WSS 운영 세션의 같은 `connection_epoch` heartbeat 사이에서 open·최근 주문·포지션과 함께 OCO deterministic client order ID(`nested=true`), 취소 대상 broker order ID를 직접 GET한다. 필요한 targeted 조회가 하나라도 빠지면 운영 세션 recovery 자체를 거부한다.
- exact client-ID OCO나 exact broker-ID 취소 terminal 상태, 유일한 평탄화 주문이 확인되면 `RECOVERED_ACKNOWLEDGED`를 남긴다.
- OCO absent는 exact client-ID 404, open·최근 목록의 matching OCO 부재, 최소 30초 정착, 최대 1일 증거 창을 모두 요구한다. 하루를 넘긴 모호 상태, targeted 404와 generic 목록 충돌, 부분 청산·중복 matching order는 unresolved로 유지한다.
- targeted 조회 종류·mutation key·수신시각·broker order 요약을 current-epoch recovery JSON에 함께 저장한다.

## 검증

- 전체 회귀: `553 passed`.
- 변경 Python: Ruff lint, basedpyright, no-excuse 검사 통과.
- MockTransport: live endpoint 선차단, exact OCO POST body, cancel/close DELETE, request ID 누락, 422 거부 계약 통과.
- 통합 테스트: attempt 선커밋, ACK 재전송 금지, timeout 잠금, request ID 없는 명시적 거부, cancel→close 순서, exact OCO/cancel targeted GET, current-epoch 누락 차단, 30초/1일 absence 창, 모순 증거와 partial-close unresolved 경계 통과.
- 실제 Paper GET-only CLI: help 0, 누락 DB 1, 실제 v6 원장 복사본 0, 반복 실행 0.
- 실제 복사본: schema v7, `quick_check=ok`, stream recovery 10건, mutation intent/event 0건, safety plan 2건, 미해결 0건.
- 실제 반복 실행 최대 RSS: 62,636,032 bytes. POST/PATCH/DELETE는 호출하지 않았다.
- targeted 연결 뒤 별도 실제 복사본 재검증도 두 번 모두 종료코드 0이었다. 최종 상태는 schema v7, `quick_check=ok`, stream recovery 6건, mutation intent/event 0건, safety plan 0건, 미해결 0건이며 최대 RSS는 62,685,184 bytes였다. 두 실행 모두 WSS와 REST GET만 사용했다.

## 남은 게이트

현재 증거는 실제 broker mutation 성공 증거가 아니다. production 운영 세션과 CLI의 mutation 제출 표면은 닫혀 있다. 다음 열린 미국 정규장에서 ORB 한 전략·동시 1포지션·최소수량·축소 위험으로 OCO 제출, 취소, 평탄화를 각각 별도 smoke하고 request ID·trade_updates·REST·원장을 사후 대사한 뒤에만 pilot을 시작한다.

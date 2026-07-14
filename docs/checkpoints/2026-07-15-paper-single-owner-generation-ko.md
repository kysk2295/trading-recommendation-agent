# Alpaca Paper 단일 WSS·Writer 세대 경계 체크포인트

## 해결한 위험

기존 `trade_updates` ingestion과 주문 readiness는 각각 자체 WSS를 열 수 있었다. 따라서 한 연결에서 복구한 원장과 다른 연결에서 계산한 승인을 future 주문 mutation에 그대로 사용할 수 없었다.

## 구현 계약

`open_paper_operating_session(credentials, store)`는 다음 순서를 한 context 안에 고정한다.

1. 비차단 단일 Writer lease 획득
2. 중단 뒤 남은 raw receipt 순서대로 재처리
3. Alpaca Paper WSS 한 번 인증·구독
4. 같은 `connection_epoch`의 두 Pong 사이 REST recovery 저장
5. `ingest_next`와 `evaluate_order`를 비동시 직렬화
6. admission 직전 current-epoch recovery를 다시 저장
7. 복구 뒤 Writer local changes와 SQLite external `data_version`을 generation으로 고정
8. REST·원장·포트폴리오 승인 전후 epoch 또는 generation 변화 시 승인 폐기

공개 factory에는 provider, clock, stream, writer 주입 인자가 없다. 테스트 전용 seam은 별도 dependency value로 묶여 운영 생성자에서 접근하지 않는다.

## 검증

- 단일 통합 시나리오에서 WSS open 1회
- 시작 recovery 2 heartbeat, admission 직전 recovery 2 heartbeat, 활성 승인 대사 2 heartbeat
- 같은 context의 두 번째 Writer lease 거부
- context 종료 뒤 승인 재사용 거부
- current-epoch recovery 뒤 외부 SQLite commit을 주입했을 때 generation 변경 blocker 확인
- paper 실행·원장 관련 표적 회귀 235개 통과
- 실제 Alpaca Paper smoke: WSS 인증·구독과 GET recovery 1건 성공, execution detail complete
- 실제 smoke 최대 RSS 60,751,872 bytes, 3.71초

실제 smoke는 주문을 제출·교체·취소하지 않았다. POST/PATCH/DELETE 메서드는 여전히 없고, 실제 candidate admission과 보호주문·kill switch·EOD 평탄화는 다음 안전 단계다.

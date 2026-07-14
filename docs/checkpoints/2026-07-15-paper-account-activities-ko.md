# Alpaca Paper Account Activities 체결 복구 체크포인트

확인일: 2026-07-15

상태: **GET-only 체결 증거 복구 완료, 주문 mutation 계속 비활성**

## 해결한 위험

Alpaca `trade_updates` WSS는 replay cursor와 무손실 high-water 계약을 제공하지 않는다. 재연결 사이에 개별 부분체결 frame을 놓치면 REST 주문의 누적 체결량만으로 수량은 알 수 있어도 실제 execution 가격·수량 상세를 안전하게 재구성할 수 없었다.

이번 단계는 공식 Account Activities의 `FILL` 이력을 세 번째 독립 증거로 추가했다. WSS 체결이 빠졌더라도 Account Activities의 개별 체결 합계·누적값·잔량·평균가격이 같은 REST 주문과 정확히 일치할 때만 execution 상세를 복원한다. 어느 한 출처라도 모순이면 추정하지 않고 신규 admission을 차단한다.

## 공식 계약과 구현 경계

- 엔드포인트: `GET https://paper-api.alpaca.markets/v2/account/activities/FILL`
- 필드: activity ID, order ID, symbol, side, `qty`, `cum_qty`, `leaves_qty`, price, transaction time, `fill` 또는 `partial_fill`
- 페이지네이션: `direction=asc`, `page_size=100`, 마지막 activity ID를 다음 `page_token`으로 사용
- 시작 cursor: timezone-aware `after`; 최근 7일 주문 복구 구간과 동일
- 최대 100페이지 뒤에도 끝나지 않으면 불완전 이력으로 차단
- 공식 문서: [Account Activities](https://docs.alpaca.markets/us/docs/account-activities), [Paper activity type endpoint](https://docs.alpaca.markets/us/reference/getaccountactivitiesbyactivitytype-1)

공식 FILL 응답에는 별도 correction 또는 bust activity가 명시돼 있지 않다. 따라서 정정 의미를 임의로 발명하지 않는다. 같은 ID의 payload 변경, 이전 activity의 소실로 보이는 REST 누적 수량 감소, 새 ID의 모순 체결은 모두 immutable 충돌 또는 출처 불일치로 차단한다.

## 원장과 대사 불변식

- Pydantic boundary는 `extra="forbid"`로 새 필드를 조용히 버리지 않는다.
- SQLite schema v4는 `paper_account_activities`와 `paper_recovery_activities`를 추가한다.
- activity 본문과 SHA-256은 append-only이며 UPDATE/DELETE trigger가 변경을 거부한다.
- 복구 브래킷은 activity 집합 hash를 포함해 같은 heartbeat 구간의 다른 증거를 동일 복구로 덮어쓸 수 없다.
- activity ID 중복, 거래시각 역행, 알 수 없는 broker order ID, symbol·side·수량·잔량·평균가격 불일치를 모두 차단한다.
- WSS 체결이 완전하면 WSS가 계속 기본 증거다. WSS가 누락됐을 때만 정확히 일치하는 activity와 REST가 상세를 보강하며 원장에는 경고를 남긴다.

## 검증

- 전체 회귀: `485 passed`
- `uv run ruff check .`: 통과
- 변경 파일 `ruff format --check`: 통과
- `uv run basedpyright`: 오류·경고 0
- no-excuse 감사: 변경 소스 22개, 위반 0
- 변경 소스: 모두 250 pure LOC 이하
- 스키마 v3→v4 migration, 동일 activity 재생, 동일 ID 변조, 페이지 중복, WSS 누락 복구, 미지 주문 차단을 회귀 테스트로 고정
- 독립 bootstrap CLI에서 누락된 `websockets` PEP 723 의존성을 수동 QA로 발견해 재현 테스트와 실행 메타데이터를 수정

실제 Alpaca Paper GET/WSS smoke 산출물:

`outputs/.../paper_execution/live_smoke/20260715_account_activities_002/`

- bootstrap: 종료코드 0, 1.54초, 최대 RSS 80,265,216 bytes
- recovery: 종료코드 0, 3.98초, 최대 RSS 83,853,312 bytes
- SQLite `user_version=4`, recovery 1건, `integrity_check=ok`
- 현재 Paper 계좌의 최근 7일 FILL은 0건
- 보고서에 `POST/PATCH/DELETE: 비활성`과 세션 종료 뒤 admission 재사용 불가를 명시

실제 계좌가 빈 상태였으므로 비어 있지 않은 실제 FILL payload를 관찰했다는 뜻은 아니다. 부분체결·다중 페이지·충돌 경로는 공식 문서 형식의 HTTP fixture와 실제 SQLite를 사용한 통합 테스트 증거다. 수익성이나 Paper 전략 성과를 검증한 단계도 아니다.

## 다음 안전 게이트

1. 부분체결 직후 체결 수량만큼의 보호 손절·목표를 생성하고 대사
2. submit/cancel/replace timeout을 client order ID·REST·원장으로 멱등 복구
3. 일손실 USD 300 kill switch와 신규 진입 cutoff
4. 마감 전 미체결 취소와 EOD 강제 평탄화

이 네 단계가 실제 Alpaca Paper에서 검증되기 전에는 POST/PATCH/DELETE를 공개하지 않는다.

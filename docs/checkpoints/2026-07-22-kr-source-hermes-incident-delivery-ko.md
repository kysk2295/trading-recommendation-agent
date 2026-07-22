# KR Source Incomplete Hermes Incident 전달 체크포인트

작성 기준 커밋: `ac29b44014ff34f28b68beb1b5563c370134a26d`

## 판정

- 현재 KRX cycle의 source 불완전 상태를 Hermes delivery 원장에 incident로 투영하는 실제 제품 경로를 연결했다.
- production delivery event는 생성됐지만 Telegram 전송은 `retry_scheduled`로 끝났고 acknowledgement는 0건이다.
- 따라서 KR 차단 사유의 외부 전달은 시도까지 검증됐지만 Milestone 1 완료 증거는 아니다.

## 구현 계약

`run_hermes_delivery.py project-kr-cycle`은 다음 세 입력만 받는다.

- Hermes delivery SQLite
- KR source SQLite
- collection cycle ID

프로세스 현재시각의 KST 날짜와 모든 source run의 `collection_date`가 같아야 한다. 한 source라도 없거나
terminal failed일 때만 `incident/blocked_source_incomplete` 이벤트를 만든다. 네 source가 모두 성공한 cycle,
과거 cycle, source가 없는 cycle과 완료시각보다 이른 투영은 차단한다.

이벤트 identity는 source 상태와 누락 source로 결정되며 `occurred_at`은 마지막 source 완료시각이다.
따라서 벽시계가 바뀐 재실행도 같은 immutable payload를 replay하고 중복 이벤트를 만들지 않는다.

## 실제 KRX 증거

- source database: 전용 mode-`600` append-only SQLite
- cycle: `kr-m3-live-20260722-1338`
- 성공 source: LS NWS, KIS ranking, volume-surge
- 누락 source: OpenDART
- 국내 계좌·잔고·포지션·주문 호출: 0건

전용 QA delivery store에서 최초 실행은 `examined=1, inserted=1, replayed=0`, 동일 재실행은
`examined=1, inserted=0, replayed=1`이었다. 존재하지 않는 cycle은 exit `2`로 차단됐고 delivery DB도
생성하지 않았다. QA DB와 lock은 모두 mode `600`이다.

## Production 전달 결과

- production event: 1건
- kind/status: `incident/blocked_source_incomplete`
- evidence ref: 3건
- worker attempt: 1건
- 결과: `retry_scheduled`
- Telegram acknowledgement: 0건
- dead letter: 0건

stockagent gateway만 재시작했지만 gateway process 시작만으로 process-owned delivery daemon이 event를
claim하지는 않았다. 동일 프로필 설정을 사용하는 단일 one-shot worker로 실제 전송을 한 번 시도했고,
네트워크 재시도 상태를 원장에 보존했다. 중복 전송 위험 때문에 즉시 수동 재시도하지 않았다.

## 검증

- 실패 우선: 신규 command 부재로 E2E가 실패한 것을 확인
- 신규 E2E: 4 passed
- Hermes 집중 회귀: 15 passed
- CLI `--help`: 계좌·주문·자격증명·임의 timestamp 옵션 없음
- CLI 오입력: exit `2`, delivery DB 생성 없음
- CLI actual happy path/replay: insert 1, replay 1, 최종 event 1
- Ruff: 통과
- basedpyright: 0 errors, 0 warnings, 0 notes
- 전체 pytest: **3242 passed in 184.70s**
- compileall: 통과
- no-excuse 검사: 통과

## 남은 M1 운영 조건

launchd-managed single-worker는
`2026-07-22-hermes-delivery-single-worker-service-ko.md`에서 구현·배치·재시작 검증했다.

1. Telegram 연결 회복 뒤 새 current event가 acknowledgement 1건으로 끝나는지 확인한다.
2. 실제 추천 카드, 명시적 무추천과 incident가 같은 production delivery 원장에서 중복 없이 전달되는지 검증한다.

실자금 endpoint, Alpaca live endpoint, KIS·LS 주문 endpoint와 자격증명 값은 사용하거나 기록하지 않았다.

# KR source-incomplete incident 전달 체크포인트

## 제품 동작

`run_kr_same_cycle_opportunity.py`는 등록된 KR Opportunity 전략의 collection을 실제로 시작한 뒤
source coverage가 완성되지 않으면 기존 durable Hermes projector로 `INCIDENT`를 남긴다. 같은
source run 집합의 재실행은 같은 event ID로 replay되어 중복 메시지를 만들지 않는다.

strategy authority 또는 출력 경로 검증을 통과하기 전에는 fallback을 열지 않는다. 따라서 과거
incomplete source 원장이 있더라도 미등록 전략 요청은 delivery database를 만들거나 사용자 메시지를
보낼 수 없다.

## 2026-07-22 KRX 실제 증거

- KIS ranking: success, 60 rows
- LS NWS: success, 1 catalyst
- DB-only volume surge: success, 1 catalyst
- OpenDART: local credential file 부재로 missing
- 판정: `blocked_source_incomplete`, 추천·shadow entry 생성 없음

오늘 source cycle의 root INCIDENT는 Telegram timeout 3회 뒤 dead-letter 됐다. 첫 redrive도 같은
timeout으로 dead-letter 됐지만, 두 번째 durable redrive는 한 번의 attempt로 ACK됐다. source run
evidence lineage는 두 redrive에서 유지됐고 코드·설정 변경 없이 전달이 성공했으므로 당시 실패는
INCIDENT kind 또는 correlation 결함이 아니라 일시적 Telegram transport timeout으로 판정했다.

clean SHA `424b85faf81d4e1f01fb32130bddf38879f67fe7`에서 원래 cycle을 다시 projection한 결과는
`examined=1, inserted=0, replayed=1`이었다. 이미 ACK된 redrive가 있으므로 추가 redrive는 만들지
않았다.

## 검증

- RED: incomplete collection 뒤 delivery event가 0건이라 실패
- RED: 미등록 전략이 preexisting incomplete source를 사용자 INCIDENT로 전달해 실패
- 집중 회귀: `15 passed`
- 전체 회귀: `3308 passed in 190.12s`
- 저장소 전체 Ruff: 통과
- 저장소 전체 basedpyright: `0 errors, 0 warnings, 0 notes`
- OMO no-excuse: 변경 파일 무위반
- 실제 CLI `--help`: exit 0
- 잘못된 날짜: exit 2
- 임시 등록 ledger와 오늘 source 백업을 사용한 실제 CLI:
  - collection 결과: exit 1, blocked
  - Hermes event: `incident / blocked_source_incomplete` 1건
  - operator report: mode `0600`

## 안전 경계

- KIS·LS account/order endpoint와 국내 주문 mutation: 0
- Alpaca Paper 또는 live-money mutation: 0
- OpenDART credential이 없는 상태에서 추천을 합성하지 않음
- 추천·수익·체결 결과로 해석하지 않고 source coverage incident로만 전달
- Allocation Manager는 독립 executable champion 두 개가 생길 때까지 비활성

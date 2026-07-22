# KR Same-Cycle Hermes 자동 Projection 체크포인트

기준 직전 커밋: `f79db87a27648e3e98bfc60e33fe349d209e09ac`

## 판정

- KR same-cycle Opportunity producer가 성공 cycle 직후 Hermes delivery 원장에 결과를 직접 append한다.
- 현재 cycle에 후보가 있으면 후보별 `watch`, 후보가 없으면 cycle별 `no_recommendation` 한 건을 만든다.
- 같은 cycle 재실행은 exact replay이며 delivery event를 추가하지 않는다.
- 과거 outbox 전체를 다시 읽어 전송하지 않고 exact `kr/collection_cycle` evidence가 결합된 현재 결과만 사용한다.

## 입력과 권한

`run_kr_same_cycle_opportunity.py`는 기존 source DB와 experiment ledger 외에 별도
`--delivery-database`를 필수로 받는다. 세 DB 경로는 서로 달라야 하며 DB sidecar와 report/outbox 경로도
겹칠 수 없다. 이 CLI에는 account, order, broker, arm 또는 임의 URL 입력이 없다.

delivery projection은 다음 조건을 다시 확인한다.

- market은 `kr_equities`, agent family는 `opportunity_manager`
- Opportunity strategy version은 실행 policy와 일치
- Opportunity ID 중복 없음
- exact collection cycle evidence가 한 건만 존재
- `observed_at <= cycle completed_at < valid_until`

하나라도 맞지 않으면 delivery DB writer를 열기 전에 차단한다. 후보 0건은 source collection과
same-cycle preparation이 성공한 경로에서만 생성되며 source identity는 cycle ID, strategy version과
`censored_no_opportunity`로 결정된다. 발생 시각은 재실행 시각이 아니라 immutable cycle completion 시각이다.

## 전달 수명주기

Opportunity event는 기존 Hermes projection renderer를 재사용하므로 후보 symbol, rank, score와 immutable
evidence lineage를 유지한다. downstream delivery worker의 30초 current-market gate는 그대로 적용된다.
따라서 producer 또는 worker가 지연되면 Telegram 발송 없이 terminal suppression되고, 이 projection이
freshness 제한을 완화하지 않는다.

## 수동 QA

- fixture full CLI: exit `0`, delivery event 1건, kind `watch`
- 동일 fixture replay: event 추가 0건
- complete cycle + 후보 0건: `no_recommendation` 1건, replay 추가 0건
- source DB와 delivery DB alias: exit `1`, provider 수집과 DB 생성 전 차단
- CLI `--help`: `--delivery-database` 노출
- CLI 금지 표면: account/order/broker/arm/url 옵션 0개
- stdout에는 하위 read-only fixture 진행 출력만 있었고 credential과 계좌 정보는 검사·기록하지 않았다.

## 검증

- 실패 우선: projection module 부재 collection error, CLI option 부재 5개 실패
- projection/CLI/day-session 집중 회귀: 10 passed
- KR same-cycle와 Hermes 집중 회귀: 37 passed
- 전체 pytest: **3263 passed in 187.67s**
- Ruff: 통과
- basedpyright: 0 errors, 0 warnings, 0 notes
- no-excuse와 compileall: 통과

이 체크포인트는 producer와 delivery 원장 사이의 코드 경로를 닫는다. 실제 열린 KRX same-cycle 결과가
Telegram acknowledgement까지 완주한 운영 증거는 M1/M3에 남아 있다. KIS, LS와 OpenDART는 read-only로
유지했고 국내 계좌·잔고·주문 mutation, Alpaca endpoint와 실제 금융 주문은 사용하지 않았다.

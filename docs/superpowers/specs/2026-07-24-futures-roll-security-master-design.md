# Futures Roll Security Master 설계

## 목적

Institutional Multi-Market Quant Research OS Milestone 6의 미완성 경계인
futures roll security master를 provider-neutral local vertical로 추가한다.
이번 단계는 검토된 계약 manifest를 immutable roll schedule로 바꾸며 provider,
credential, 시세, 계좌 또는 주문을 열지 않는다.

## 입력 계약

입력은 하나의 root future와 동일 venue, currency, timezone, multiplier를 공유하는
2~32개 만기 계약이다. 각 계약은 stable instrument identity와 provider alias,
listing·last-trade·expiration, settlement type, optional first-notice date,
active-from과 roll-at을 포함한다.

- physical settlement는 first-notice date가 필수이며 `roll_at`은
  first-notice와 last-trade보다 모두 빨라야 한다.
- cash settlement는 first-notice date가 없어야 하며 `roll_at`은
  last-trade보다 빨라야 한다.
- 계약은 last-trade 순서로 정렬되고 identity, alias, date가 모두 고유해야 한다.
- 첫 계약 이후 `active_from`은 직전 계약의 `roll_at`과 정확히 같아야 한다.
- source observation보다 미래에 알려진 계약은 master에 들어갈 수 없다.

## 출력과 조회

검증된 전체 payload의 canonical JSON SHA-256을 master ID로 사용한다. CLI는
`futures_roll_security_master_<id>.json`과 aggregate report를 mode 600으로
게시한다. exact replay는 기존 artifact를 재사용한다.

as-of resolver는 `active_from <= as_of < roll_at`인 계약이 정확히 하나일 때만
active contract를 반환한다. gap, overlap, roll 이후 미등록 계약과 관측시각 이전
조회는 fail-closed한다.

## 안전과 검증

- input은 current-user private regular file, mode 600, single hard link여야 한다.
- output directory와 artifact는 owner-only이며 content-addressed 이름을 재검증한다.
- report에는 root, venue, 계약 수, active contract 존재 여부와 mutation 0만 쓴다.
- fixture happy/replay, physical/cash 규칙, gap/overlap, future knowledge,
  non-private input과 bad CLI를 검증한다.
- 외부 network와 broker/account/order mutation은 항상 0이다.

실제 CME·ICE 등 provider adapter와 licensed source evidence는 별도 checkpoint다.
fixture master를 실제 거래 가능 contract coverage나 roll 성과로 표현하지 않는다.

# KR Volume Surge V2 파생 체크포인트

날짜: `2026-07-16 KST`

## 결과

- 입력: append-only KR 원장의 같은 cycle `kis_ranking` terminal run·receipt·catalyst·observation
- 출력 adapter: `kis-ranking-volume-surge-v2`
- symbol 계약: v1 `[0-9]{6}` replay 유지, v2 `[0-9A-Z]{6}`
- v2 lineage: 각 symbol에 exact upstream KIS catalyst ID와 source 관측시각 보존
- 비율 계약: `accumulated_volume / average_volume`, Decimal precision 28과 `ROUND_HALF_EVEN`
- source run: provider receipt를 위조하지 않는 receipt-free derived `volume_surge` terminal run
- provider network·credential·현재 호가·broker·외부 mutation: 0건

이 결과는 저장된 KIS 거래량 evidence를 canonical metric으로 파생하고 재생할 수 있다는 증거다. 종목 추천, 실시간 진입가, 체결 가능성 또는 수익성 증거가 아니다.

## 상태기계와 인과성

파생기는 exact `kis-kr-ranking-v1` source run이 성공했는지 먼저 확인하고, 그 run의 receipt count·observation count·receipt item lineage·payload checksum·source identity를 다시 검증한다. 같은 cycle의 KIS `volume` 행만 사용하지만 threshold로 행을 버리지 않으며, 0행이면 유효한 빈 v2 snapshot을 만든다.

평균거래량 0, upstream evidence 불일치, 실패한 KIS source와 source보다 이른 파생시각은 성공으로 축소하지 않는다. 이미 terminal인 derived run은 clock·credential·network를 열기 전에 그대로 replay한다. catalyst append 뒤 중단된 경우에도 최초 파생시각을 재사용해 observation과 terminal run을 복구하며 immutable payload를 다시 만들지 않는다.

`news`, `dart`, `kis_ranking` provider source는 계속 receipt가 필수다. receipt-free terminal observation 예외는 직접 파생한 `volume_surge`에만 허용되며, observation의 receipt link도 없어야 한다.

## 기존 Production KIS 원장 로컬 파생

2026-07-16 bounded read-only KIS smoke가 남긴 성공 원장을 provider 재호출 없이 입력으로 사용했다.

- upstream HTTP receipt: 2
- upstream KIS ranking row: 60
- v2 volume symbol: 30
- 영문 포함 6자리 symbol: 7
- 첫 파생: 신규 catalyst 1, 신규 observation 1, source `success`
- exact replay: 신규 catalyst 0, 신규 observation 0, restart no-op
- DB·aggregate 보고서: mode `600`

실행 중 KIS client, 자격증명 loader, Alpaca, LS, OpenDART와 주문 코드는 호출하지 않았다. 기존 실패 cycle과 raw receipt도 삭제하거나 성공으로 변경하지 않았다.

## 검증

- focused model·projection·store·derivation·CLI suite: `67 passed in 0.62s`
- 전체 pytest: `1549 passed in 22.13s`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- CLI `--help`: exit 0, cycle ID·date·database·output directory 네 입력과 help만 노출
- 잘못된 `../escape` cycle ID: exit 2, 파일 생성 없음
- committed fixture 첫 파생: symbol 1, 신규 catalyst 1, 신규 observation 1
- fixture exact replay: 신규 catalyst 0, 신규 observation 0
- DB·보고서 mode `600`, raw payload·symbol·provider message·credential·token·hash·path 비노출

## 구현 커밋

- `e23dd14 docs: design KR volume surge v2 derivation`
- `9f771ce docs: plan KR volume surge v2 derivation`
- `ee63116 feat: version KR volume surge symbols`
- `169d44a feat: verify volume surge v2 lineage`
- `5dc32f0 docs: clarify derived source receipt contract`
- `db0de97 feat: derive KR volume surge evidence`
- `2e8d255 feat: add KR volume surge derive CLI`

## 다음 KR milestone

1. DART → LS NEWS → KIS ranking → volume surge → coordinator를 같은 새 cycle ID로 직렬 실행하는 날짜별 오케스트레이터
2. provider 단계·SQLite Writer 비병렬화와 terminal replay의 credential·network 선차단
3. fixture E2E 뒤 bounded production 동일-cycle과 새 KR Opportunity projection 검증
4. LS/KIS 호가·VI·minute bar·수급 read-only evidence
5. KR quote·VI·가격제한 risk gate와 shadow TradeSignal

국내 계좌·잔고·포지션·주문 경로는 현재와 다음 오케스트레이터 milestone 모두 범위 밖이다.

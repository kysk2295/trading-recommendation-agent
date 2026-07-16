# KIS 국내 랭킹 read-only collector 체크포인트

날짜: `2026-07-16 KST`

## 결과

- 공식 live origin: `https://openapi.koreainvestment.com:9443`
- 등락률 순위: `GET /uapi/domestic-stock/v1/ranking/fluctuation`, TR `FHPST01700000`
- 거래량 순위: `GET /uapi/domestic-stock/v1/quotations/volume-rank`, TR `FHPST01710000`
- 성공 production smoke: GET 2건, raw-first receipt 2건, canonical catalyst 60건, observation 60건
- 성공 cycle exact replay: 신규 GET·receipt·catalyst·observation 0건
- 최종 `kis_ranking` source run: `success`
- DB·Writer lock·aggregate report: mode `600`
- KIS 계좌·잔고·포지션·주문 endpoint: 0건
- Alpaca·LS·OpenDART·외부 메시지·금융 mutation: 0건

이 결과는 KIS 국내 랭킹 source 계약과 read-only 연결 가용성 증거다. 종목 추천 품질, 실시간 진입가, 체결 가능성 또는 수익성 증거가 아니다.

## Raw-first와 재시작

client는 위 두 fixed-origin GET 외 경로를 노출하지 않고 redirect를 따르지 않는다. 각 응답은 status, content type, request/continuation metadata와 원본 bytes를 parser보다 먼저 append-only receipt로 확정한다. 검증된 행만 receipt item lineage가 있는 cycle-local `kis_ranking` catalyst로 투영한다.

`500`, `502`, `503`, `504`만 80ms 뒤 한 번 재시도한다. 종류별 최대 10페이지이며, continuation·schema·날짜·중복 symbol/rank가 계약과 다르면 이미 저장한 receipt와 앞선 catalyst를 유지한 채 immutable failed run으로 닫는다. terminal run 재실행은 local ledger에서 즉시 반환한다. terminal 없이 receipt만 남은 재시작은 `incomplete_restart`로 닫으며 두 경로 모두 fixture, 현재 날짜 gate, credential, token과 HTTP를 열지 않는다.

## 보존한 실패와 원인

첫 production read-only cycle은 exact GET 2건과 receipt 2건을 먼저 보존한 뒤 `invalid_response`로 실패했다. 등락률 catalyst 30건은 유지했고 실패 run을 삭제하거나 성공으로 바꾸지 않았다. 값이나 종목을 출력하지 않은 aggregate 점검으로 거래량 30행 중 7행이 문자를 포함한 공식 6자리 단축코드임을 확인했다.

parser의 숫자 전용 가정을 `[0-9A-Z]{6}` 계약으로 보정하고 별도 cycle에서 exact GET 2건을 다시 실행해 receipt 2건과 catalyst 60건으로 성공했다. 따라서 개발 중 production 국내 랭킹 GET은 총 4건이며 모두 위 두 read-only allow-list endpoint다. 후속 `KrVolumeSurgePayload`와 KR instrument 계약도 같은 영숫자 단축코드를 명시적으로 버전 확장해야 하며, 문자를 포함한 코드를 조용히 폐기하면 안 된다.

## 검증

- focused KIS ranking suite: `62 passed in 0.81s`
- 전체 pytest: `1498 passed in 22.36s`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- CLI `--help`: exit 0, 승인된 다섯 입력과 help만 노출
- 잘못된 `../escape` cycle ID: exit 2, DB 생성 없음
- committed fixture 첫 실행: receipt 2, catalyst 2, exit 0
- fixture exact replay: 신규 receipt 0, 신규 catalyst 0, exit 0
- production exact replay: 신규 receipt 0, 신규 catalyst 0, credential·token·HTTP 호출 없음
- DB·보고서 mode `600`, terminal·보고서의 raw payload·provider message·credential·token·hash·path 비노출

## 구현 커밋

- `7d3120c feat: define KIS KR ranking read contract`
- `be3bab9 test: add safe KIS KR ranking fixtures`
- `aae1c47 fix: preserve malformed KIS ranking responses`
- `b08af30 feat: collect raw-first KIS KR rankings`
- `5c970fd feat: add KIS KR ranking collector CLI`
- `9f6337f fix: honor live KIS ranking contracts`
- `254e32e fix: validate complete KIS ranking fixtures`

## 다음 KR milestone

1. KR instrument와 `KrVolumeSurgePayload` symbol을 `[0-9A-Z]{6}`로 버전 확장
2. 저장된 같은-cycle KIS 거래량 ranking evidence만 읽는 canonical `volume_surge` terminal source run
3. DART·LS NEWS·KIS ranking·volume surge의 날짜별 순차 orchestrator와 기존 DB-only coordinator 연결
4. LS/KIS 체결·호가·VI·minute bar read-only evidence
5. KR quote·VI·가격제한 risk gate와 shadow TradeSignal

국내 계좌·주문 경로는 현재와 다음 read-only milestone 모두 범위 밖이다.

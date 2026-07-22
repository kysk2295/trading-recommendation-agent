# KR M3 장중 Ranking·Volume·LS NWS Source 체크포인트

작성 기준 커밋: `9e9646a8ce0dc76ddac18d9ef1af5988e5eaac22`

## 판정

- 열린 KRX 세션에서 KIS 국내 랭킹 read-only source, LS NWS read-only WebSocket과 DB-only volume-surge 파생이 같은 새 cycle에서 실제로 성공했다.
- 첫 LS NWS 시도는 정상 ACK 뒤 실제 뉴스의 영숫자 `realkey`를 숫자 전용 파서가 거절해 실패했으며, 실패 run과 raw receipt는 append-only 증거로 보존했다.
- 전체 four-source cycle, Opportunity, RecommendationCard와 shadow lifecycle은 생성하지 않았다.
- 따라서 이 증거는 Milestone 3의 실제 source 가용성 증거이며 Milestone 3 완료 증거가 아니다.

## 실제 실행

- 실행일: 2026-07-22 KST
- 실행 시각대: KRX 정규장 중
- collection cycle: 전용 `kr-m3-live-20260722-1322`
- 저장소: 전용 mode-`600` append-only SQLite
- KIS 호출 범위: 국내주식 랭킹 GET-only
- 국내 계좌·잔고·포지션·주문 호출: 0건

### 최초 ranking·volume 실행 결과

- KIS ranking raw receipt: 2건
- KIS ranking catalyst: 60건
- volume-surge 입력 symbol: 30건
- volume-surge 신규 catalyst: 1건
- volume-surge 신규 observation: 1건
- source run: `kis_ranking=success`, `volume_surge=success`
- final collection cycle: 0건

### LS NWS 최초 실패와 수정

- 최초 cycle: 전용 `kr-m3-live-20260722-1322`
- LS 구독 ACK: 성공
- 두 번째 실제 뉴스 frame: `invalid_packet`
- 원인: 실제 24자 opaque `realkey`에 ASCII 대문자가 포함되지만 파서가 숫자만 허용했다.
- 수정: 24자 길이와 ASCII 대문자·숫자 제한을 유지하며 실제 wire shape를 허용했다.
- 실패 source run: append-only `failed/invalid_packet`으로 보존
- LS 계좌·잔고·포지션·주문 호출: 0건

### 수정 후 같은-cycle 3-source 결과

- 성공 cycle: 전용 `kr-m3-live-20260722-1338`
- LS NWS raw receipt: 2건(구독 ACK 1건, 뉴스 1건)
- LS NWS catalyst: 1건
- KIS ranking raw receipt: 2건
- KIS ranking catalyst: 60건
- volume-surge 입력 symbol: 30건
- volume-surge catalyst 및 observation: 각 1건
- source run: `news=success`, `kis_ranking=success`, `volume_surge=success`
- DART source run: 없음
- final collection cycle: 0건

### 재실행 결과

- 신규 LS NWS receipt와 catalyst: 각 0건
- 신규 ranking receipt: 0건
- 신규 ranking catalyst: 0건
- 신규 volume-surge catalyst: 0건
- 신규 volume-surge observation: 0건
- database와 모든 Markdown report mode: `600`

### 검증 게이트

- 세 CLI의 실제 `--help`: 통과
- 현재일이 아닌 production ranking 입력: exit `2` 차단, provider 호출 전 종료
- 존재하지 않는 cycle의 volume-surge 입력: exit `2` 차단
- LS NWS `duration-seconds=0`: exit `2` 차단, provider 호출 전 종료
- LS parser 실제 wire regression: **129 passed**
- 관련 runtime replay: 신규 행 0건
- `uv run pytest -q`: **3238 passed in 187.51s**
- `uv run ruff check .`: 통과
- `uv run basedpyright`: **0 errors, 0 warnings, 0 notes**
- compileall: 통과

## 완료하지 않은 이유

- 현재 호스트에 OpenDART 설정 파일이 없어 four-source cycle을 완전하다고 표시할 수 없다.
- 당일 장전 등록된 production composite/trial이 없어 장중에 이를 소급 생성하지 않았다.
- source coverage가 불완전하므로 Opportunity, 추천 카드, shadow entry/exit와 Telegram 결과를 강제로 만들지 않았다.

## 다음 실제 세션 준비

1. 다음 KRX 거래일 장전까지 OpenDART read-only 설정과 production composite/trial을 준비한다.
2. 같은 당일 cycle에서 DART, LS NWS, KIS ranking, volume-surge 네 terminal source를 완주한다.
3. fresh Opportunity를 onboarding한 뒤 minute별 GET-only collect, shadow entry/exit와 일일 결과를 실행한다.
4. 카드 또는 명시적 no-recommendation 결과를 Hermes delivery store와 Telegram에 연결한다.

이 결과는 후보 원천의 당일 가용성과 immutable replay 증거일 뿐, 추천 품질이나 수익성 증거가 아니다.

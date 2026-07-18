# Alpaca US Security Master 체크포인트

- 날짜: 2026-07-19
- 범위: Paper assets GET-only raw-first instrument/alias snapshot
- 실제 외부 GET: 3건
- account/order endpoint: 0건
- POST/DELETE mutation: 0건

## 구현

- canonical Paper trading origin의 `GET /v2/assets?status=all&asset_class=us_equity`만 허용하며 redirect와 live origin을 요청 전에 차단한다.
- HTTP response bytes를 파싱 전에 mode-600 append-only SQLite에 저장한다. raw와 snapshot table의 UPDATE/DELETE trigger를 금지한다.
- active 상태이고 지원 listed venue와 canonical symbol을 가진 asset만 투영한다. instrument ID는 stable Alpaca asset UUID에 `alpaca:` namespace를 결합한다.
- provider class `us_equity`는 ETF 여부를 추정하지 않고 현재 계약의 `equity`로 기록한다. 세부 ETF 분류는 별도 authoritative source가 생길 때 correction event로 추가해야 한다.
- snapshot은 관측시각부터만 유효하다. latest reader는 연결 raw payload SHA-256과 receipt ID를 재계산하고 snapshot payload의 instrument/alias geometry를 다시 검증한다.
- KIS scanner projector는 optional security-master store가 주어지면 foundation 내부 fixture alias 대신 이 snapshot을 사용한다. snapshot은 미래일 수 없고 1일을 넘길 수 없으며 foundation은 `ready`이고 fixture provider가 없어야 한다.

## 실제 QA

- 첫 두 GET은 실제 provider schema 확장과 21개 비식별 name 공백을 strict parser가 발견해 raw 보존 후 차단했다.
- field 이름·타입·행 개수만 집계해 계약을 보정했고 asset 값이나 자격증명은 출력하지 않았다.
- 세 번째 GET은 raw 33,351행, active instrument 13,011개, mode-600 store/report로 ready 종료됐다.
- actual latest snapshot의 공개 symbol 하나를 synthetic KIS Opportunity에 결합해 `alpaca:` instrument와 `us_equities.broad_scanner` replay identity를 확인했다.

## 검증

- focused security-master/scanner/KIS contracts: **25 passed**
- full repository: **2187 passed**
- Ruff: 통과
- basedpyright: 0 errors/warnings
- no-excuse: 변경 production module 위반 0건
- CLI help, required-argument error, fixture happy path, actual GET happy path: 통과

## 다음 경계

actual security master만으로 SIP feature evidence가 ready라고 주장하지 않는다. broad scanner foundation은 완전한 KIS 6개 랭킹·NYSE halt coverage와 이 snapshot으로 만들고, SIP는 그 결과 선택된 bounded candidate의 분봉 feature evidence에만 사용한다.

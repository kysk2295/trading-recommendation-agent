# KIS KR 거래일 게이트 체크포인트

## 공식 read-only 계약

- 근거는 KIS 공식 `open-trading-api` commit `885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc`의 국내휴장일조회 sample이다.
- 허용 HTTP 계약은 exact `https://openapi.koreainvestment.com:9443`, `GET /uapi/domestic-stock/v1/quotations/chk-holiday`, TR `CTCA0903R` 하나다.
- query는 `BASS_DT`, `CTX_AREA_FK`, `CTX_AREA_NK`만 사용한다. redirect와 다른 origin은 credential 전송 전에 차단한다.
- 계좌번호, 잔고, 포지션, 주문 파라미터와 mutation endpoint는 사용하지 않는다.

## Raw-first 거래일 증거

- 응답 JSON bytes, base date, 수신시각과 SHA-256을 frozen receipt로 먼저 확정한다.
- projection은 날짜 순서·중복과 `Y/N` 값을 strict하게 검증하고 `bzdy_yn`, `tr_day_yn`, `opnd_yn`이 모두 참인 날짜만 열린 session으로 인정한다.
- source commit, adapter version, base date, 관측시각, raw SHA와 ordered day rows가 content-addressed snapshot ID에 결합된다.
- private SQLite는 base date당 raw BLOB과 snapshot을 append-only로 보존한다. mode 600/current owner/regular file/single hard link와 exact schema·trigger·payload hash를 read마다 재검증한다.

## KR day trial 결합

- `run_kr_theme_day_trial.py register`는 `--calendar-store`가 필수다.
- 등록 KST 날짜와 같은 base date의 snapshot이 정확히 하나 있어야 하고, 관측 뒤 5분 이내이며 목표 session row가 open이어야 한다.
- snapshot ID는 trial `evidence_budget`과 `data_version`에 결합된다. 이후 start·terminal·Reviewer exact replay도 이 ID의 누락·중복·변형을 거부한다.
- 휴장일, stale snapshot, missing/public/tampered store는 trial append 전에 fail-closed한다.

## 검증

- 공식 GET path/TR/query·wrong origin·transport redaction, holiday skip, inconsistent flags, raw/schema tamper와 private replay: 통과
- 관련 KR day: **44 passed**
- 전체 회귀: **2698 passed**
- Ruff, changed-file format, basedpyright `0 errors, 0 warnings`, compileall, no-excuse: 통과
- actual CLI: register help, missing-store exit `1`, fixture-backed register/replay exit `0`, report/calendar mode `600`
- provider network, credential file, account/order endpoint와 external mutation: `0`

## 남은 운영 작업

fixture는 계약과 인과성 증거를 검증하지만 실제 KRX session 관찰을 대신하지 않는다. 다음 운영 단계는 자격증명 값을 노출하지 않는 bounded current-date read-only GET을 하루 한 번 실행해 calendar store를 채우고, 열린 정규장 market-data smoke와 scheduler가 같은 snapshot을 소비하도록 연결하는 것이다. 그 뒤 KR Reviewer event를 multi-market lifecycle v2에 연결하되 실제 표본·comparator·multiple-testing evidence 전 자동 champion 승격은 계속 금지한다.

# LS NWS Read-Only News Collector 설계

## 1. 범위

이 설계는 다중 시장 Research OS Milestone 3과 KR Theme Phase T0의 production `news` source adapter다.

```text
LS OAuth token POST
-> exact LS NWS001 WebSocket subscription
-> frame bytes append before parse
-> strict NWS title parse
-> canonical NEWS catalyst + receipt lineage append
-> immutable news source run
```

이번 단계는 LS증권의 실시간 `NWS` 뉴스 제목 패킷만 연결한다. 기사 본문 `t3102`, 주식 체결·호가·VI·봉, 외인·기관·프로그램 수급, 기술지표, LLM, 현재가 위험 gate, TradeSignal과 shadow fill은 포함하지 않는다. LS 계좌·잔고·포지션·주문 API는 구현하거나 호출하지 않는다.

공식 계약은 [LS OPEN API 이용안내](https://openapi.ls-sec.co.kr/howto-use)와 [LS OPEN API Python 적용예제](https://openapi.ls-sec.co.kr/howto-sample)를 기준으로 한다.

대화에 노출된 기존 App Key와 App Secret은 폐기·재발급 대상이다. 해당 값은 파일, 로그, 도구 호출, 테스트, 커밋 또는 실제 LS 요청에 사용하지 않는다. 운영 QA는 재발급한 자격증명이 안전한 로컬 파일에 준비된 뒤 별도 단계에서 수행한다.

## 2. 비교한 접근

### 2.1 LS의 뉴스·시세·수급·계좌 기능을 한 client에 통합 - 기각

읽기 경계와 거래 경계가 한 객체에 섞이고, 허용하지 않은 계좌등록이나 주문 TR이 잘못 노출될 수 있다. source별 실패와 재시작도 독립적으로 감사하기 어렵다.

### 2.2 뉴스 제목을 수신 즉시 파싱해 catalyst만 저장 - 기각

서버가 실제로 보낸 frame과 wire 순서를 잃는다. 파서 변경을 과거 원문에 재적용할 수 없고, 파싱 중 중단되면 수신 사실도 사라진다.

### 2.3 NWS 전용 allow-list stream과 raw-first collector 분리 - 채택

OAuth, WebSocket transport, NWS parser, collection state machine을 각각 작은 모듈로 둔다. stream은 `tr_type=3`, `tr_cd=NWS`, `tr_key=NWS001` 한 메시지만 만들 수 있다. 수신 frame bytes를 기존 append-only source receipt에 먼저 확정한 뒤 파싱하고, terminal source run 재실행은 자격증명·token·network 없이 저장 결과를 반환한다.

## 3. 공식 LS 경계

### OAuth

- exact base URL: `https://openapi.ls-sec.co.kr:8080`
- exact method/path: `POST /oauth2/token`
- content type: `application/x-www-form-urlencoded`
- fields: `grant_type=client_credentials`, `appkey`, `appsecretkey`, `scope=oob`
- redirect: 금지
- TLS 인증서 검증: 필수

공식 예제의 `verify=False`는 사용하지 않는다. HTTP status, content type, JSON object와 access token 형식을 bounded하게 검증한다. 응답 body, URL query, request body와 provider message는 저장하거나 출력하지 않는다. 이 milestone은 token cache를 만들지 않고 collection process당 token을 한 번만 발급한다.

### NWS WebSocket

- exact URL: `wss://openapi.ls-sec.co.kr:9443/websocket`
- proxy와 compression: 비활성
- bounded open/read/close timeout, frame size와 queue
- 구독 header: access token과 `tr_type=3`
- 구독 body: `tr_cd=NWS`, `tr_key=NWS001`
- redirect 또는 최종 URL 변경: token 전송 전에 차단

WebSocket 계좌등록·해제 타입 `1/2`, 다른 realtime TR, 모의투자 port `29443`, `/stock/accno`와 `/stock/order`는 이 adapter에서 사용할 수 없다. 연결 종료는 context close로 처리하며 별도 해제 메시지 `tr_type=4`도 이번 범위에서 보내지 않는다.

공식 예제에는 별도 subscription acknowledgement가 정의되어 있지 않다. 따라서 canonical subscription 전송 뒤 bounded window 동안 정상적으로 frame을 수신하거나 timeout까지 연결이 유지되면 transport 성공으로 본다. 인증·구독 거절 형태가 fixture 또는 운영 aggregate smoke에서 확인되면 raw receipt를 보존한 새 control-frame parser milestone로 추가한다.

## 4. 비밀 계약

- 기본 secret path: `~/.config/trading-agent/ls.env`
- 허용 설정: `LS_APP_KEY`, `LS_APP_SECRET` 정확히 한 번씩
- current-user-owned regular file, symlink 금지, exact mode `600`
- 값은 비어 있지 않은 공백 없는 printable ASCII이며 각각 20..256자
- credential와 access token dataclass field는 `repr=False`
- token, key, secret, Authorization, subscription JSON, raw auth response와 provider message는 예외·terminal·보고서에 포함하지 않음

fixture mode는 secret path를 받을 수 없고 token이나 network를 사용하지 않는다. 운영 mode도 terminal source run이 이미 있으면 secret을 읽거나 token을 발급하지 않는다.

## 5. Transport와 frame 계약

`LsNwsRawFrame`은 다음 값만 가진다.

```text
sequence
received_at
wire_kind = text | binary
raw_payload bytes
```

WebSocket wrapper는 `recv(timeout)` 결과가 text면 UTF-8 bytes로 바꾸고 binary면 bytes를 그대로 보존한다. frame 내용은 로깅하지 않는다. timeout은 bounded collection window의 정상 종료이고, handshake·connection close·socket 오류는 sanitized transport failure다.

기존 `KrSourceReceipt` schema v1에는 HTTP status 필드만 있다. 이 첫 stream adapter는 schema migration 대신 각 frame receipt에 다음 의미를 고정한다.

- `http_status=101`: 이 frame을 전달한 연결이 성공적인 WebSocket upgrade를 통과했다는 inherited transport status
- `content_type=application/json`
- `request_key=ls:nws:frame:<6자리 sequence>:<wire_kind>`
- `payload_blob`: exact frame bytes

`http_status=101`은 개별 frame의 application status를 뜻하지 않는다. wire kind는 request key에 보존한다. 여러 stream adapter에서 이 임시 표현이 반복되기 전에 stream-native receipt schema를 별도 설계한다.

## 6. NWS parser와 catalyst 계약

수신 JSON은 duplicate key를 거부하고 top-level, `header`, `body`에 extra field를 허용하지 않는다.

```text
header:
  tr_cd = NWS
  tr_key = NWS001
body:
  date
  code
  realkey
  bodysize
  time
  id
  title
```

- `date`는 유효한 `YYYYMMDD`, `time`은 유효한 `HHMMSS`다.
- `realkey`는 공식 예제 형식인 24자리 숫자다.
- `bodysize`와 `id`는 bounded unsigned decimal string이다.
- `code`는 빈 문자열 또는 bounded printable ASCII다. 의미가 공식 예제에 정의되지 않았으므로 종목코드로 해석하지 않는다.
- `title`은 trim된 비어 있지 않은 control-character-free 문자열이다.
- published timestamp는 `date + time + Asia/Seoul`이며 receipt `received_at`보다 미래면 거부한다.
- frame 날짜가 명시한 collection date와 다르면 실패한다.

canonical catalyst payload는 위 공식 값을 이름 변경 없이 하나의 flat UTF-8 JSON object로 보존한다. `tr_cd`, `tr_key`도 포함한다. `title`이 top-level이므로 기존 deterministic keyword baseline이 nested 임의 순회 없이 사용할 수 있다.

```text
source = news
source_record_id = ls-nws://news/<realkey>
publisher_id = None
published_at = parsed KST timestamp
first_observed_at = frame received_at
content_type = application/json
```

`id`의 publisher 의미가 공식 문서로 확인되지 않았으므로 publisher로 추정하지 않는다. 같은 cycle에서 `realkey`가 중복되면 두 번째 raw receipt까지 보존한 뒤 `duplicate_news` 실패로 종결한다.

## 7. 수집 상태기계

```text
terminal news source run 존재
  -> secret/token/network 없이 exact 저장 결과 반환
terminal run 없음
  -> bounded input 검증
  -> start clock
  -> lazy source opener: fixture 또는 OAuth + NWS
  -> frame receive
  -> raw receipt append
  -> parse/date/causality/duplicate validation
  -> canonical catalyst + observation + receipt link append
  -> max frame 또는 collection timeout
  -> exact receipt/observation count 재검증
  -> terminal news source run append
```

`source_run_id`는 `<collection_cycle_id>:news`, adapter version은 `ls-nws-v1`이다. 성공적인 연결에서 뉴스가 없었던 bounded window는 성공 0건이다. token, handshake, stream, malformed JSON, schema, date, causality 또는 duplicate 오류는 safe failure code를 가진 terminal failed run으로 보존한다. 받은 receipt와 앞서 저장한 catalyst는 삭제하지 않는다.

한 source run은 terminal이 된 뒤 같은 cycle에서 재시도하거나 성공으로 변경하지 않는다. 운영 재시도에는 새 cycle ID가 필요하다. news source run 하나만으로 네 source `KrCatalystCollectionCycle`을 확정하지 않는다.

## 8. Fixture와 CLI

`run_ls_nws_collect.py`는 다음을 명시적으로 받는다.

```text
--collection-cycle-id
--collection-date YYYY-MM-DD
--duration-seconds
--max-frames
--database
--output-dir
--fixture-manifest 또는 --secret-path
```

fixture manifest는 path-contained raw frame 파일, 순차 sequence, fixed receipt time과 wire kind를 가진다. committed fixture는 synthetic 제목만 사용한다.

CLI 보고서는 source 상태, failure code, receipt/catalyst/observation/new-row count와 restart 여부만 mode `600` 파일에 기록한다. 제목, code, realkey, id, checksum, frame bytes, 자격증명, token, endpoint와 provider message는 terminal과 보고서에서 제외한다. failed source run도 원장과 aggregate 보고서를 남긴 뒤 nonzero로 종료한다.

## 9. 검증

- config: owner, exact mode, regular file, symlink, UTF-8, exact key set, value bounds, repr redaction
- OAuth: exact URL/method/form fields, redirect 금지, TLS client, status/content/JSON/token validation, secret 비노출 오류
- WebSocket: exact initial/final URL, canonical NWS-only subscription, account/order type 부재, text/binary raw frame, timeout와 sanitized transport error
- parser: official shape, duplicate JSON key, extra/missing field, date/time/realkey/title bounds, future timestamp
- ledger: raw receipt precedes parser, `101` semantics, wire kind request key, causal lineage, partial failure, duplicate, zero-news success
- restart: terminal success/failed run 모두 opener·credential·token·network 0회
- fixture: path containment, symlink, order, raw bytes
- CLI: `--help`, bad input, fixture success/failure/restart, report redaction, DB/report mode `600`
- full pytest, Ruff, basedpyright

실제 LS OAuth/NWS, OpenDART, KIS, Alpaca, LLM, broker와 외부 메시지 호출은 이 체크포인트 QA에서 0건이어야 한다.

## 10. 다음 LS read-only milestones

1. NWS `realkey`를 이용한 공식 `t3102` 기사 본문 raw-first enrichment
2. LS 국내 랭킹과 거래량 급증 REST adapter로 남은 KR source run 연결
3. LS 체결·호가·VI realtime과 minute-bar receipt를 quote/risk evidence로 분리
4. immutable bar에서 VWAP, ATR, RSI, MACD, RVOL과 breakout feature를 로컬 계산해 historical replay와 live가 같은 indicator kernel 사용
5. 외인·기관·프로그램 REST snapshot을 시점이 명시된 supply feature로 추가
6. 네 source coordinator와 scheduler를 연결한 일일 KR forward-validation loop

LS 계좌·잔고·포지션·주문 endpoint와 실제 국내 거래는 후속 범위에도 포함하지 않는다.

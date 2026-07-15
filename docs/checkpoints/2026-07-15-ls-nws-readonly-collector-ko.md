# LS NWS Read-Only News Collector 체크포인트

## 범위

다중 시장 Research OS Milestone 3과 KR Theme Phase T0의 production `news` source adapter로 LS증권 `NWS001` 실시간 뉴스 제목 경로를 추가했다.

```text
exact LS OAuth POST
-> NWS001-only WebSocket subscription
-> frame bytes receipt append
-> strict NWS/KST parse
-> canonical NEWS catalyst + observation lineage
-> terminal news source run
```

이번 검증은 synthetic committed fixture와 injected HTTP/WebSocket transport만 사용했다. 실제 LS OAuth·WebSocket 요청, OpenDART, KIS, Alpaca, LLM, broker와 외부 메시지 호출은 0건이다. 대화에 노출된 App Key와 App Secret은 사용·저장·로그·커밋하지 않았으며 폐기·재발급 전에는 운영 smoke를 수행하지 않는다.

## 공식 계약

[LS OPEN API 이용안내](https://openapi.ls-sec.co.kr/howto-use)와 [LS OPEN API Python 적용예제](https://openapi.ls-sec.co.kr/howto-sample)를 기준으로 다음 allow-list를 고정했다.

- REST base: `https://openapi.ls-sec.co.kr:8080`
- token: `POST /oauth2/token`, client credentials 네 form field
- redirect 금지, TLS 인증서 검증 유지
- WebSocket: `wss://openapi.ls-sec.co.kr:9443/websocket`
- subscription: `tr_type=3`, `tr_cd=NWS`, `tr_key=NWS001`
- proxy/compression 비활성, bounded timeout/frame/queue

LS `/stock/accno`, `/stock/order`, WebSocket 계좌등록·해제 `tr_type=1/2`, 임의 realtime TR과 국내 주문 코드는 추가하지 않았다.

## 구현

### 비밀과 OAuth 경계

- 기본 secret: `~/.config/trading-agent/ls.env`
- 설정: `LS_APP_KEY`, `LS_APP_SECRET` 정확히 한 번씩
- current-user-owned regular file, symlink 금지, exact mode `600`
- 20..256자 printable non-whitespace ASCII
- credential와 access token은 repr에서 제외
- token 요청은 secret을 URL query에 넣지 않고 form body로 전송
- status/content type/64 KiB response cap/JSON/token 형식을 검증하고 provider body·message를 렌더링하지 않음
- token cache는 이번 범위에 없으며 collection process당 한 번만 발급

### NWS-only WebSocket

final URL을 token 전송 전에 다시 검증하고 canonical NWS subscription 하나만 보낸다. text frame은 UTF-8 bytes로 바꾸고 binary frame은 exact bytes를 보존한다. receive timeout은 bounded window의 정상 종료이고 handshake·close·socket 오류는 sanitized failure다.

공식 예제에 별도 subscription acknowledgement가 없으므로 fixture 계약도 첫 수신을 NWS data frame으로 둔다. 운영 aggregate smoke에서 control frame이 확인되면 raw를 보존하는 별도 parser milestone로 추가하며 현재 parser가 임의 응답을 성공으로 추정하지 않는다.

### Strict packet과 raw-first 원장

- duplicate JSON key와 extra/missing field 거부
- exact `NWS`/`NWS001`, valid `YYYYMMDD`/`HHMMSS`, 24자리 `realkey`
- title trim/control/length, code/id/bodysize bounds
- `date + time + Asia/Seoul` publication이 receipt보다 미래면 차단
- collection date 불일치 차단
- canonical flat JSON에 공식 필드와 `tr_cd`, `tr_key` 보존
- identity: `ls-nws://news/<realkey>`

각 frame은 parser 전에 기존 `KrSourceReceipt`에 append한다. `http_status=101`은 개별 application status가 아니라 해당 frame을 전달한 연결이 성공적인 WebSocket upgrade를 통과했다는 inherited transport status다. wire kind는 `ls:nws:frame:<sequence>:<wire_kind>` request key에 보존한다.

malformed/date/future/duplicate/stream/token 실패는 받은 receipt와 앞선 catalyst를 삭제하지 않고 safe failure code의 immutable news source run으로 종결한다. 정상 연결의 zero-news bounded window는 성공 0건이다. terminal success와 failed run 모두 같은 cycle 재실행에서 secret·token·network 없이 저장 결과를 반환한다.

### Fixture와 CLI

`run_ls_nws_collect.py`는 cycle ID, KST 날짜, duration, frame cap, database와 output directory를 명시적으로 받는다.

- fixture mode: path-contained manifest의 synthetic text/binary frame을 순서대로 재생
- production mode: lazy opener 안에서만 strict secret, exact OAuth와 exact NWS stream 조합
- fixture와 secret path 동시 입력 차단
- terminal restart는 production mode로 호출해도 secret path를 읽지 않음
- source run 실패도 mode-600 원장과 aggregate 보고서에 보존한 뒤 nonzero 종료
- 보고서는 title, code, realkey, id, checksum, frame, credential, token, endpoint와 provider message를 포함하지 않음
- 보고서는 temporary mode-600 file을 fsync한 뒤 원자적으로 교체

## 검증

- LS focused suite: `140 passed`
- 전체 pytest: `1310 passed in 20.61s`
- Ruff 전체: 통과
- basedpyright 전체: 오류 0, 경고 0
- `uv run python run_ls_nws_collect.py --help`: exit 0
- invalid date: exit 2, DB·보고서 생성 없음
- committed fixture 첫 실행: receipt 2, catalyst 2, observation link 2, news source run success
- 같은 cycle 재실행: 신규 receipt 0, 신규 catalyst 0, source opener no-op
- DB, Writer lock, aggregate report mode: 모두 `600`
- 보고서에서 fixture title·realkey·credential setting marker 비노출
- 실제 LS/OpenDART/KIS/Alpaca/LLM/외부 메시지 호출: 0
- broker·계좌·주문 mutation: 0

fixture 결과는 LS 운영 가용성, 기사 coverage, 테마 분류 정확도, 종목 추천 품질 또는 수익성 증거가 아니다.

## 커밋

- `1a46894 feat: add guarded LS credential config`
- `c775283 feat: add sanitized LS OAuth client`
- `4594554 feat: parse LS NWS packets strictly`
- `98422de feat: add NWS-only LS stream`
- `3f9b020 feat: collect LS news raw first`
- `0f69423 test: add deterministic LS NWS fixture`
- `9caa97b feat: add LS NWS collection CLI`
- `a25be34 fix: make LS NWS collector executable`

## 다음 단계

1. 재발급 credential로 aggregate-only OAuth/NWS bounded smoke와 control-frame 형태 확인
2. NWS `realkey` 기반 공식 `t3102` 기사 본문 raw-first enrichment
3. KIS/LS 국내 랭킹과 거래량 급증 adapter로 네 source production cycle 완성
4. LS 체결·호가·VI·minute bar를 별도 immutable market-data receipt로 수집
5. raw bar에서 VWAP, ATR, RSI, MACD, RVOL과 breakout feature를 historical/live 공통 kernel로 계산
6. 외인·기관·프로그램 REST snapshot과 KR quote/VI/가격제한 risk gate 연결

LS 계좌·잔고·포지션·주문 endpoint와 실제 국내 거래는 계속 범위 밖이다.

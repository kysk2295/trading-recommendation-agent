# LS NWS 구독 ACK 운영 체크포인트

날짜: `2026-07-16 KST`

## 결과

- 실제 LS OAuth와 `wss://openapi.ls-sec.co.kr:9443/websocket` bounded read-only 연결: 통과
- allow-list subscription: `tr_type=3`, `tr_cd=NWS`, `tr_key=NWS001`
- 성공 구독 ACK: raw-first receipt 1건
- ACK 뒤 causally valid 뉴스: raw-first receipt 1건, catalyst 1건
- 최종 source run: `success`
- DB·Writer lock·aggregate report: mode `600`
- LS 계좌·잔고·포지션·주문 호출: 0건
- Alpaca·KIS·OpenDART·외부 메시지·금융 mutation: 0건

이 결과는 LS 뉴스 제목 수집 계약과 연결 가용성 증거다. 테마 분류 정확도, 종목 추천 품질, 체결 가능성 또는 수익성 증거가 아니다.

## 보존한 실패와 원인

첫 rotated-credential smoke는 OAuth와 WebSocket upgrade 뒤 받은 control frame을 뉴스 packet으로 해석해 `invalid_packet`으로 끝났다. frame receipt와 failed source run은 삭제하거나 성공으로 바꾸지 않았다.

ACK parser와 collector 상태기계를 적용한 첫 재검증에서는 ACK는 통과했지만 다음 실제 뉴스 frame이 다시 `invalid_packet`으로 끝났다. 값·제목·원문을 출력하지 않고 JSON 키·타입만 점검해, LS 공식 예제의 7개 body field 외에 `categoryid`와 `codeaccu`가 함께 온 것이 원인임을 확인했다. 공식 예제는 여전히 기존 7필드 형태를 게시하므로 parser는 다음 두 형태만 허용한다.

1. 공식 7필드 NWS data packet
2. 같은 7필드에 bounded `categoryid`와 `codeaccu`가 모두 있는 운영 확장형

한 확장 field만 있거나 unknown extra, malformed 값, ACK 전 뉴스, duplicate ACK, ACK 없는 종료는 실패한다. provider message와 raw payload는 terminal·보고서·문서에 남기지 않는다.

## 상태기계와 재시작

```text
connect
→ raw ACK receipt append
→ strict success ACK
→ zero or more raw NWS data receipts
→ strict data parse + causal catalyst append
→ bounded timeout/frame cap
→ immutable terminal source run
```

adapter version은 `ls-nws-v2`다. terminal run 재실행과 orphan receipt 복구는 첫 저장 receipt를 로컬에서 다시 분류해 ACK 여부만 복원하며 secret, OAuth, WebSocket 또는 fixture manifest를 열지 않는다. 실패 run도 append-only로 유지하고 새 운영 시도는 새 cycle ID를 사용한다.

## 검증

- focused LS suite: `120 passed`
- 전체 pytest: `1339 passed in 21.11s`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- CLI `--help`: exit 0
- 잘못된 cycle ID: exit 2, DB 생성 없음
- committed fixture: receipt 3, catalyst 2, ACK 확인, exit 0
- 실제 bounded smoke: receipt 2, catalyst 1, ACK 확인, exit 0
- 보고서·terminal에 credential, token, provider message, title, realkey, raw hash 비노출

## 남은 KR vertical

1. NWS `realkey` 기반 `t3102` 기사 본문 raw-first enrichment
2. KIS 국내 랭킹과 거래량 급증 source run
3. 네 production source의 exact complete cycle
4. LS/KIS 체결·호가·VI·minute bar read-only evidence
5. KR quote·VI·가격제한 risk gate와 shadow TradeSignal
6. 장후 평가·Reviewer·experiment lifecycle 연결

국내 계좌·주문 경로는 현재와 후속 read-only milestone 모두 범위 밖이다.

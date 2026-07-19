# KR theme day EOD minute·shadow exit 체크포인트

## 마지막 완료 봉

intraday collector는 직전 완료 minute만 요청하므로 15:29~15:30 마지막 봉을 받으려면 close 이후 별도 phase가 필요하다. `run_kis_kr_market_collect.py --eod-minute`는 official open session의 15:30~15:31에만 분봉 GET 하나를 허용하고 requested minute를 15:29로 고정한다.

raw response는 먼저 append한다. 이후 envelope와 provider success뿐 아니라 output rows에 requested minute가 실제 포함되는지 검증한다. 잘못된 minute payload도 삭제하지 않지만 complete collection으로 사용하지 않는다. EOD phase는 현재가·호가 GET, account와 주문 endpoint를 열지 않는다.

## exit child

`run_kr_theme_day_shadow_exit.py`는 exact trial의 entry·exit store를 먼저 재생한다. 기존 terminal entry는 다시 계산하지 않고 open entry만 처리한다. 같은 symbol과 KST session의 evaluated time 이전 minute receipts를 canonical bars로 만든 뒤 entry filled time의 minute ceiling부터 이어지는 path를 기존 exit projector에 전달한다.

- 한 봉에서 stop과 target이 함께 닿으면 stop-first
- 첫 target에서 종료
- 15:30까지 terminal이 없으면 마지막 완료 봉 close의 time exit
- 중도 path는 pending이며 exit artifact를 만들지 않음

report는 terminal/open/pending/new count만 기록하고 trial ID, symbol, price, raw payload와 path를 노출하지 않는다.

## 검증

- focused EOD collector/exit child·CLI: `7 passed`
- related collector/exit/terminal: `26 passed`
- 전체 회귀: `2740 passed`
- Ruff 전체와 changed-file format: 통과
- basedpyright: `0 errors, 0 warnings`
- compileall, 신규 production no-excuse: 통과
- actual CLI help, EOD fixture minute-only, target exit/replay: 통과
- provider credential/network, 국내 account/order mutation: `0`

## 다음 단계

durable KR day session manifest와 append-only phase audit이 pre-open trial register/start, intraday collector→entry→exit cycle, EOD minute catch-up, post-session terminal→Reviewer→lifecycle runner를 하나의 exact session identity로 연결한다.

# M5 Issuer-direct Announcement Raw-first 체크포인트

## 범위

Institutional Multi-Market Quant Research OS Milestone 5의 SEC·licensed news 다음 경계로 회사 직접 발표 source onboarding과 bounded GET collector를 추가했다. 이 경로는 권리가 검토된 issuer RSS/Atom metadata만 historical research와 shadow forward에 제공한다. 종목 ranking, 추천, Paper 계좌·주문, lifecycle 승격과 Allocation Manager 권한은 없다.

저장소에 실제 회사 endpoint와 자동 접근 권리를 임의로 선언하지 않았다. committed fixture는 IANA reserved `.example` domain만 사용한다. 실제 provider GET은 권리 검토가 완료된 별도 mode-600 onboarding이 있을 때만 열린다.

## Onboarding 계약

private onboarding은 다음을 하나의 immutable request identity에 고정한다.

- exact `issuer_direct/*` source ID, issuer ID와 bounded symbol 집합
- HTTPS endpoint, item-link allow-list와 이용약관 URL
- 자동 접근 허용 여부와 365일 이내 권리 검토 시각
- 계약 유효기간, 최대 요청률과 freshness SLO
- raw·derived retention, 삭제 의무와 append-correction
- historical research·shadow forward만 허용하고 redistribution은 `none`
- RSS 2.0 또는 Atom 형식과 최대 100개 item

자동 접근이 허용되지 않았거나 권리 검토가 오래됐거나 `paper_recommendation` use가 있으면 provider·fixture와 output store를 열기 전에 차단한다. endpoint는 HTTPS, no userinfo, no fragment, public hostname 형식이어야 하고 item URL은 onboarded host에 속해야 한다.

## Raw-first 수집·재생

- GET은 redirect와 transport retry를 사용하지 않고 전체 45초, identity encoding, wire 2 MiB로 제한한다.
- HTTP success/error raw bytes는 reversible base64와 SHA-256을 가진 mode-600 immutable receipt로 XML parsing 전에 확정한다.
- malformed XML도 receipt를 잃지 않고 `response_structure` terminal로 닫는다.
- DTD와 entity, foreign host, duplicate provider ID, future published time, unsafe URL을 fail-closed한다.
- normalized event는 issuer·symbol, opaque provider event ID, published time, URL, title SHA와 raw receipt ID만 갖는다. terminal과 redacted report에는 headline 원문을 복제하지 않는다.
- terminal replay는 fixture·network보다 먼저 끝난다. receipt-only crash는 저장된 raw bytes를 다시 parsing해 terminal을 복구한다.
- capability registry는 성공 run만 `complete`, `us_equities:bounded_issuer`로 투영한다. entitlement는 non-real-time historical·shadow와 무재배포를 유지한다.

## CLI

onboarding과 fixture raw response는 current-owner mode-700 parent 아래 mode-600 regular single-link file이어야 한다. macOS에서는 `/tmp` symlink 대신 `/private/tmp`를 사용한다.

```bash
install -m 600 \
  tests/fixtures/issuer_announcement/onboarding.json \
  /private/tmp/issuer-announcement-onboarding.json
install -m 600 \
  tests/fixtures/issuer_announcement/feed.xml \
  /private/tmp/issuer-announcement-feed.xml

uv run --offline --script run_issuer_announcement_collect.py \
  --collection-id issuer-announcement-fixture-001 \
  --requested-at 2026-07-23T20:15:00Z \
  --onboarding /private/tmp/issuer-announcement-onboarding.json \
  --fixture-response /private/tmp/issuer-announcement-feed.xml \
  --store-dir /private/tmp/issuer-announcement-store \
  --registry /private/tmp/issuer-announcement-registry.sqlite3 \
  --output-dir /private/tmp/issuer-announcement-report
```

production에서는 `--fixture-response`만 생략한다. collector는 onboarding에 고정된 endpoint로 credential 없는 GET 하나만 수행한다.

## 검증

- TDD: 신규 CLI가 없는 import failure에서 시작
- focused: `4 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings`
- actual PEP 723 CLI `--help`: exit `0`
- missing onboarding: redacted exit `2`
- fixture happy: raw receipt `1`, metadata `1`, capability·entitlement append `1/1`
- missing fixture exact replay: network `0`, append `0/0`, replay `yes`
- malformed XML: raw receipt 보존 뒤 `response_structure`, exit `2`
- receipt, terminal, registry와 report mode: `600`
- report의 symbol, headline, host와 local path 노출: `0`
- production issuer GET: `0`
- credential·broker·account·position·order mutation: `0`

Apple의 공식 웹사이트 이용약관은 자동 page scraping을 허용하지 않으므로 Apple Newsroom을 임의 onboarding하지 않았다. Microsoft Investor Relations는 회사 발표를 공식 게시하지만 현재 exact RSS endpoint와 자동 연구 수집 권리를 이 체크포인트에서 확정하지 못했으므로 역시 production manifest를 만들지 않았다.

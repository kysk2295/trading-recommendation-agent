# SEC EDGAR Submissions Read-Only 체크포인트

## 범위

Institutional Multi-Market Quant Research OS Milestone 5의 첫 미국 공시 source로 SEC EDGAR submissions JSON의 최근 filing snapshot을 추가했다. 이 경로는 규제 공시 evidence 수집만 수행하며 추천, 전략 승격, 계좌 조회 또는 주문 권한이 없다.

## 공식 계약

- endpoint: `GET https://data.sec.gov/submissions/CIK##########.json`
- 인증 토큰은 없지만 조직·애플리케이션과 연락처를 선언한 `User-Agent`가 필요하다.
- client는 exact SEC origin과 no-redirect GET 하나만 제공한다.
- 전체 HTTP 작업은 wall-clock 45초에 강제 중단하며 wire body와 압축 해제 body는 각각 64 MiB로 제한하고 원문 wire bytes와 content encoding을 파싱 전에 mode-600 append-only SQLite에 확정한다.
- SEC의 요청 한도는 source capability 계약에서 최대 10 requests/second를 넘지 않아야 한다.

공식 근거는 SEC의 [EDGAR API 안내](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [Developer Resources](https://www.sec.gov/about/developer-resources), [Webmaster FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions)를 따른다.

## 구현

- `SecSubmissionRawResponse`는 0-byte HTTP 오류, JSON 외 MIME와 gzip/deflate encoding도 원문으로 보존하고 parser가 `200 application/json`을 별도로 요구한다.
- recent filing column 길이, document/response/accession CIK 일치, accession, 접수시각, XBRL flag와 문서 identity를 strict하게 검증한다.
- 같은 accession의 동일 canonical event는 기존 version을 재사용한다. payload가 달라지면 이전 version ID를 부모로 하는 새 immutable version을 만든다.
- correction observation은 이전 version의 최신 관측시각보다 빠를 수 없으며 모든 저장시각은 UTC로 canonicalize한다.
- receipt, filing version, run과 observation table은 update/delete trigger로 append-only이며 SQLite structural integrity, exact DDL signature, foreign key, raw payload hash, duplicated run columns, run/receipt/observation lineage와 전체 accession version ancestor chain을 매번 확인한다.
- caller snapshot, receipt-backed failure code와 저장된 ordered observation은 exact raw receipt를 다시 파싱한 deterministic projection과 같아야 한다. 모든 public store write는 기존 전체 receipt, run, ordered observation과 version chain을 먼저 재생하며, snapshot filing CIK, accepted-at 대 receipt observation, failed-run provenance/history count와 linear correction 순서가 모순되면 mutation 전과 replay에서 모두 거부한다.
- terminal success·failure run과 terminal 이전에 남은 orphan receipt는 CLI가 provider, fixture, User-Agent file과 HTTP client를 열기 전에 exact replay 또는 deterministic terminal 복구한다.
- receiptless transport terminal로 끝난 collection key에는 늦은 receipt를 추가하지 않으며, 재시도는 새 collection ID를 사용한다.
- database와 report alias, symlinked report 경로, foreign version-0 SQLite와 invalid store는 provider fetch와 store mutation 전에 거부한다.
- fixture payload는 파일 크기를 먼저 확인하고 bounded read하며 issuer와 additional-history 내부 metadata는 이 checkpoint에서 소비하지 않으므로 rejection 조건으로 사용하지 않는다.
- fixture와 production CLI는 raw body, CIK, accession, 회사명과 User-Agent를 보고서에 기록하지 않는다.

## 운영

production User-Agent는 저장소 밖 `~/.config/trading-agent/sec.env`에 다음 한 설정으로 둔다.

```text
SEC_USER_AGENT=<application-or-organization> <contact-email>
```

파일은 현재 사용자 소유 regular file, single hard link, mode `600`이어야 한다. 실제 연락처가 없는 임의 값은 사용하지 않는다.

```bash
uv run python run_sec_edgar_collect.py \
  --collection-id sec-YYYYMMDD-001 \
  --cik 0000320193 \
  --database outputs/us_regulatory/sec_edgar.sqlite3 \
  --output-dir outputs/us_regulatory/sec/latest
```

## 검증 경계

fixture는 raw-first success, correction version, HTTP 오류 raw 보존, transport terminal failure와 provider-free replay를 검증한다. 이 체크포인트에서는 유효한 실제 연락처 User-Agent를 임의 생성하지 않았으므로 production SEC GET은 0건이다.

- focused SEC: `62 passed`
- SEC + OpenDART related: `114 passed`
- full suite: `2915 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings`
- compileall과 `git diff --check`: 통과
- manual CLI: `--help`, invalid CIK, fixture first run `2/2`, missing-User-Agent terminal replay `2/0`, missing fixture·User-Agent orphan 복구, DB/report alias 거부, DB/report mode `600`, directory mode `700`

최종 release gate의 redacted exact-SHA 리뷰 기록은 저장소 밖 `~/.codex/review-evidence/sec-edgar-<short-sha>.md`에 보존한다. Git commit 내부에서 자신의 SHA를 참조할 수 없으므로 Reviewer 입력에는 해당 absolute path를 별도로 제공한다.

`filings.recent`만 canonical event로 저장한다. 응답의 `filings.files`는 개수만 기록하며 추가 history 파일은 아직 가져오지 않는다. 다음 M5 경계는 additional history의 bounded raw-first 수집, SEC source capability registry projection과 issuer/company-announcement evidence다.

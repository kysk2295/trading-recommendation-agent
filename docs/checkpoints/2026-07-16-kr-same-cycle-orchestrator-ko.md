# KR Same-Cycle Source Orchestrator 체크포인트

## 완료 범위

- `run_kr_same_cycle_collect.py`가 하나의 `collection_cycle_id`와 KST 날짜에서 `DART → LS NWS → KIS ranking → volume_surge → DB-only coordinator`를 순서대로 실행한다.
- stage와 SQLite writer는 병렬 실행하지 않는다. 각 stage 뒤 원장을 다시 읽어 source run ID, adapter version, collection date가 정확한 terminal contract인지 확인한다.
- OpenDART는 `opendart-list-v2` date-bound resume preflight를 사용한다. exact terminal replay면 fixture, credential, HTTP client를 열지 않으며 날짜 또는 adapter 불일치는 fetch 전에 닫힌다.
- 네 source run이 모두 terminal이면 provider stage를 전혀 호출하지 않고 coordinator만 replay한다. terminal source 실패는 뒤의 source를 계속 순서대로 검사한 뒤 immutable `complete=false` cycle과 nonzero 종료로 보존한다.
- production에서 새 source stage가 필요할 때는 collection date가 실행 시점 KST 날짜와 정확히 같아야 한다. historical production replay는 네 terminal contract가 이미 있을 때만 provider 없이 허용된다.
- 새 CLI는 executable mode로 배포된다. aggregate coverage CSV와 한국어 요약은 atomic mode `600`으로 기록하며 cycle ID, fixture path, raw payload, hash, credential을 담지 않는다.

## 검증

- focused OpenDART replay tests: `15 passed`
- focused orchestration/coordinator tests: `13 passed`
- CLI 및 기존 source CLI 회귀 묶음: `55 passed`
- 전체 자동 게이트: `uv run pytest -q` -> `1570 passed`
- 정적 분석: `uv run ruff check .` -> `All checks passed!`
- 타입 검사: `uv run basedpyright` -> `0 errors, 0 warnings, 0 notes`
- 수동 CLI QA: `--help`는 cycle ID, date, database, output directory, fixture root, help만 노출하고 성공했다.
- 수동 CLI QA: malformed cycle ID는 exit 2로 provider/DB 생성 전에 차단됐고 새 임시 디렉터리에 파일을 남기지 않았다.
- 수동 CLI QA: committed synthetic fixture의 첫 실행은 네 source와 complete cycle을 순서대로 완료했고 aggregate report 두 파일의 mode가 `600`이었다.
- 수동 CLI QA: 같은 DB에 `--fixture-root` 없이 재실행하면 `재시작 4건`으로 성공했다. source stage output이 다시 생성되지 않았다.
- 자동 CLI 회귀: 네 provider stage entrypoint를 모두 실패시키도록 patch한 terminal replay가 성공했고, failed DART fixture는 immutable incomplete cycle과 exit 1을 남겼다.

## 이번 단계에서 하지 않은 일

- production OpenDART, LS, KIS provider 호출: 0건
- KIS/LS account endpoint, 국내 주문, 국내 계좌: 0건
- Alpaca 호출 또는 Paper mutation: 0건
- LLM, 외부 메시지, Telegram 전송: 0건

이 명령은 raw-first source coverage collection이다. KR Opportunity projection, TradeSignal, quote/VI/가격제한 risk gate, shadow fill, 성과 판정과 국내 주문 권한을 만들거나 열지 않는다.

## 다음 단계

현재 KST·자격증명·정상 endpoint 조건이 모두 맞을 때만 별도 bounded production same-cycle을 read-only로 수집한다. coverage가 완전히 확정된 뒤에는 별도 immutable manifest로 KR keyword Opportunity projection을 실행한다. 둘 중 어느 단계도 국내 주문이나 TradeSignal 권한을 열지 않는다.

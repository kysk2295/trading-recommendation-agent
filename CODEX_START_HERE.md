# Codex 작업 시작점

## 프로젝트 목표

미국 급등주 후보를 시점 가용 데이터로 관찰하고, 검증된 전략의 추천과 Alpaca Paper 전진검증을 한 프로젝트에서 운영한다. 실제 자금 주문은 영구 금지한다.

## 현재 상태

- KIS 읽기 전용 인증·랭킹·분봉 연결 완료
- KIS 국내 KRX 등락률·거래량 순위의 current-date raw-first `kis_ranking` source run과 restart no-network CLI 구현
- OpenDART `opendart-list-v2` date-bound terminal replay preflight 구현. 정확한 terminal source run이면 fixture·자격증명·HTTP를 열지 않고, 날짜나 adapter 계약이 다르면 fetch 전에 차단
- 숫자 전용 `volume_surge` v1 replay를 유지하면서 실제 KIS 단축코드 `[0-9A-Z]{6}`와 행별 upstream catalyst ID를 보존하는 v2 계약 구현
- 저장된 같은-cycle KIS 거래량 evidence만 읽어 canonical `volume_surge` v2 catalyst·observation·receipt-free derived terminal run을 append하는 DB-only 상태기계와 CLI 구현
- `run_kr_same_cycle_collect.py`가 DART → LS NEWS → KIS ranking → volume surge를 같은 날짜·cycle ID로 직렬 처리한 뒤 DB-only coordinator를 호출. complete cycle은 0, terminal source 실패 cycle은 `complete=false`와 nonzero로 보존하며 full terminal replay는 어떤 stage도 호출하지 않음
- `run_kr_theme_projection.py`는 resolved input ledger·SQLite sidecar와 KR JSONL/report output의 경로·hard-link 충돌을 ledger open 전 차단하고, zero-projection replay를 포함해 committed keyword projection outbox와 한국어 요약을 mode `600`으로 유지. provider·LLM·broker·TradeSignal·국내 주문 호출 없음
- `run_us_swing_shadow.py`는 완료된 US 일봉 21세션에서 신고가·RVOL v1의 다음 세션 conditional signal을 만들고 dedicated mode-600 append-only SQLite에 다중세션 shadow event를 기록. fixture replay는 network·credential·Paper를 열지 않으며 production은 current NYSE post-close와 정렬된 1~50 symbol universe를 credential 전 검사한 뒤 Alpaca data GET만 허용
- bounded production KIS 원장 local-only 파생에서 랭킹 60행 중 거래량 30행과 영문 포함 코드 7개를 보존하고 terminal replay 신규 0행 확인. provider·credential·network·broker 호출 없음
- KIS 주간거래 `BAQ/BAY/BAA`를 프리마켓·정규장과 분리한 원시 랭킹 forward 수집 완료
- 매 cycle KIS 원시 랭킹 행·출처·선택 여부 CSV 누적
- 선택 후보 완료 정규장 1분봉과 최초 관찰 시각 SQLite 영속화
- 최초 선택 후보의 당일 watchlist 유지, 랭킹 탈락 후 신규 신호 없는 추적
- 스캔 다음 1분봉 시가·완료 세션만 사용하는 forward outcome·임계값 진단 CLI
- ORB 81개 인접값·편도 5/10/20bp·최대 10포지션 전진성과 CLI
- `--strategy vwap_reclaim`으로 ORB와 섞지 않는 첫 눌림목 VWAP reclaim paper 실행
- `--strategy hod_breakout`으로 첫 HOD·2~8봉 base·거래량 재확대 paper 실행
- `--strategy gap_and_go`로 첫 5분 갭 지속·실패 분류 paper 실행
- 모든 KIS 전략은 NYSE 공식 현재 거래정지·호가·spread·슬리피지 위험 게이트를 포트폴리오 선정 전에 통과
- 전체 위험판정 모집단 보존과 spread·slippage·왕복비용 27개 인접값별 최대 10개 재선정 CLI
- 전체 위험판정 후보의 volume·ADV 보존과 등락률·가격·거래대금·volume/ADV 81개 조합별 재선정 CLI
- 현재 거래일의 최신 완료 봉만 신규 추천에 사용
- 과거 봉은 워밍업에만 사용해 과거 추천 역생성을 차단
- 시장 폐장·데이터 지연·호가 없음 상태에서 추천 차단
- 종목별 마지막 처리 봉을 저장하고 재시작 시 새 완료 봉만 처리
- 날짜별 영속 runner가 공급자 부분 오류를 실패 cycle로 감사하고 다음 cycle은 계속 실행
- 정규장 종료를 매 cycle 전에 다시 확인해 폐장 뒤 호출 중단
- NYSE 공식 2026~2028 휴장·조기폐장 반영, 미게시 연도 fail-closed
- 추천 ID 기반 SQLite immutable outbox와 JSONL·한국어 카드 projection
- 정규장 종료 시 마지막 완료 봉 가격으로 열린 추천 same-day `time_exit`
- paper 종료 거래의 5/10/20bp 비용·연도별 결과·bootstrap CI·fallback 비율 대시보드 구현
- 2026-07-13 실제 KIS 폐장 재검증에서 후보 3개, 당일 정규장 분봉·추천 0개 확인
- 2026-07-13 실제 KIS 폐장 위험 표본 163개와 27개 인접값 분석 저장, 폐장 호가 결손으로 식별력 없음 판정
- 같은 폐장 표본의 volume·ADV 163개를 완전 저장하고 81개 스캐너 조합을 재선정했지만 후행성과·opening gap은 데이터 결손 유지
- 실제 키 2회 QA에서 랭킹 1,200행·관찰 시각 2개 누적 확인
- Alpaca Paper 계좌·미체결 주문·포지션 GET-only adapter 완료
- 실행 원장 Single Writer 잠금·append-only schema·계좌 fingerprint 결합 완료
- GET-only bootstrap과 fail-closed preflight 실제 빈 Paper 계정 검증 완료
- Paper 시장시계 GET과 `trade_updates` 인증·구독·Ping/Pong, reconnect별 고유 `connection_epoch` 완료
- 활성 WSS의 두 Pong 사이 REST·단일 원장 대사와 공개 의존성 주입 없는 활성 세션 전용 승인 상태기계 완료
- 브로커 주문·포지션·원장 intent 기반 부분체결 단일 노출, 기존 노출별 손절·최소비용 위험 재계산과 신규 수량 내부 산정 완료
- 2026-07-14 실제 Paper 계정에서 WSS 인증·구독·Pong과 활성 연결 내부 계좌·시계·미체결·포지션·원장 대사 통과
- 읽기 client는 GET-only를 유지하고 mutation adapter는 단일 Writer/current-epoch 운영 세션 내부에서만 사용
- entry·보호 OCO·cancel/EOD 평탄화 공개 운영 메서드는 모두 명시적 `PaperMutationArm`이 필수
- 체결된 parent intent 하나에 대한 보호 OCO와 안전계획의 cancel·exact-position flatten smoke CLI 실행 가능. 실제 Paper POST/DELETE 검증은 아직 0건
- 안전조치 smoke는 계획과 같은 REST snapshot에서 1 entry order·1 position·1 OCO·1 symbol 및 합산 100 USD를 mutation 전에 강제하고, mutation 뒤 current-epoch 대사 실패를 성공이나 일반 차단으로 축소하지 않음
- 부분체결 누적 수량이 기존 보호 OCO보다 커지면 schema v9 source-bound DELETE만 먼저 실행하고, terminal 대사가 끝난 다음 호출에서만 새 deterministic client ID와 exact 수량으로 replacement OCO를 제출
- 보호 OCO cancel·replacement는 현재 5초 REST/WSS·ACTIVE 계좌 대사·브로커/로컬 정규장·15:55 ET 이전 게이트를 요구하며 timeout·재시작·한 leg fill·양 leg 경합을 fail-closed 복구
- `LaneId`·서로 다른 intraday/swing/regime 실행정책·보수적 risk contract·manifest·전용 account binding·experiment scope·final daily snapshot 계약 구현
- execution schema v9를 유지한 채 별도 lane registry schema v1의 append-only Writer/query-only Reviewer reader와 무네트워크 bootstrap CLI 구현
- 일일 연구 원장은 schema v2 exact scope로만 표본을 누적하고 schema v1 row는 파일을 재작성하지 않은 채 역사적 intraday scope로 투영
- ORB intraday final snapshot producer와 GET/WSS-only CLI 구현. 장 종료·5초 freshness·flat broker·registry/execution/readiness account 결합·exact daily scope·execution hash가 모두 맞아야 append하며 replay는 1행 유지
- 독립 Reviewer와 별도 review ledger schema v1 구현. Reviewer는 query-only lane registry·exact daily record·adaptive JSON만 읽고 false-only 전략/주문 권한 권고를 append하며 replay는 1행, 근거 변경은 immutable conflict
- ORB 장후 forward-validation runner 구현. snapshot 성공 뒤에만 Reviewer를 실행하고 두 단계 audit·redacted aggregate report를 남기며 주문권한·자동 승격·scheduler 역할은 없음
- ORB watch의 기존 metrics→daily record→adaptive 체인에 opt-in scheduled lane 단계를 연결. 네 경로 all-or-none·ORB-only이며 upstream 성공 뒤에만 snapshot→Reviewer runner를 호출하고 실패는 watch에 전파
- lane registry·review ledger·execution ledger와 분리된 global experiment ledger schema v1 구현. hypothesis/version/trial 등록과 trial/lifecycle event는 append-only이며 single Writer·query-only Reader·canonical key·전체 chain 검증을 사용
- global experiment ledger schema v2에 immutable `ResearchSource` catalog와 기존 hypothesis에 연결되는 `ResearchHypothesisCard`를 추가. local-only JSON preregistration CLI는 공개 근거의 주장·한계·반증 기준을 보존하고 exact v1 ledger에는 table 추가 외 기존 행 재작성이 없으며, strategy version·trial·Reviewer·lifecycle·Paper 권한은 변경하지 않음
- intraday strategy version은 사람이 읽는 parameter-set base와 exact code version SHA-256 digest를 함께 사용한다. 새 clean commit은 기존 hypothesis를 재작성하지 않고 별도 append-only strategy version·lifecycle registration을 남기며, daily record·Reviewer·snapshot·ORB trial·Lifecycle Controller는 같은 code-coupled identity를 다시 검증한다
- trial의 completed·failed·censored terminal 결과를 모두 보존하고, lifecycle은 `idea→historical→experimental_shadow→experimental_paper→challenger→paper_champion` 및 suspended/rejected 닫힌 전이표와 next-session as-of projection을 구현
- local-only `run_experiment_ledger_bootstrap.py`가 exact intraday manifest·네 scope를 먼저 검증한 뒤 현재 네 전략을 `experimental_shadow`로 이관. v1 ledger는 Reader 조회 전 Writer lease에서 v2로 올린다. 2026-07-16 runtime ledger에서 새 code version/lifecycle 4/4 append와 exact 0/0 replay, mode 600 report를 확인했으며 broker mutation은 없음
- deterministic Lifecycle Controller v1 구현. exact ORB manifest/scope·finalized flat snapshot·Reviewer event·현재 global lifecycle chain을 다시 검증하고 성숙 구간의 명확한 5일 열화만 다음 NYSE 세션 `suspended`로 append하며 exact replay는 새 event를 만들지 않음
- Controller의 `collecting`·`shadow_continue`·`diagnose`는 상태 유지, `early_stop`·`comparison_ready`·`promotion_review`는 증거 계약 미완성으로 차단. 복구·reject·challenger·champion·주문권한·위험예산 변경은 없음
- local-only `run_lifecycle_controller.py` 구현. credential·HTTP·broker·execution·Portfolio Manager를 import하지 않고 report에서 path·key·hash·strategy·raw reason을 제외하며 broker mutation은 0건
- ORB NYSE 거래일마다 독립 `shadow_forward` trial을 pre-open에 등록하고 정규장에 시작한 뒤 exact daily/adaptive/snapshot/review evidence로 `completed`·`censored` terminal을 확정하는 서비스 구현
- 같은 세션의 네 closed phase nonzero audit가 있을 때만 `failed` terminal을 허용하고, artifact·parent JSONL·scope·코드·데이터·비용·포트폴리오 계보 변조는 fail-closed 처리
- local-only `run_orb_forward_trial.py`와 ORB watch opt-in `--experiment-ledger` 연결 구현. register/start는 provider scan보다 먼저 실행하고 장후 child 실패는 audited terminal로 닫으며 주문·상태·champion·allocation 권한은 없음
- source-bound US swing 신고가·RVOL 신호 하나를 global `shadow_forward` trial 하나로 pre-open 등록하고 query-only swing shadow terminal로만 completed를 확정하는 수직선 구현. 별도 mode-600 append-only swing Reviewer는 artifact를 다시 해시해 `continue_collection`만 기록하고 lifecycle·champion·allocation·Paper 권한은 모두 false
- local-only `run_swing_shadow_trial.py`가 `register → start → finalize → review`를 한 동작씩 실행. provider·credential·endpoint·arm·force 옵션이 없고 report는 redacted mode 600이며 external broker mutation은 0건
- armed entry·safety smoke는 하나의 intraday pilot risk contract를 공유하며 100 USD·10 USD·1포지션·30 USD·편도 20bp·risk fraction 1/3000을 유지
- GET-only `run_alpaca_paper_safety.py`도 active intraday lane risk contract를 명시적으로 주입해 entry·armed safety mutation과 같은 USD 100·USD 10·1포지션·USD 30·편도 20bp 권위를 사용
- contract-only data foundation이 `DataSourceId`·entitlement·capability/SLO·`StrategyDataRequirement`·point-in-time instrument/alias/corporate action·canonical event를 검증하고, 명시된 primary/fallback만 평가해 `ready`·`research_only`·`blocked_by_data`를 결정
- offline local-only `run_data_foundation_check.py`는 fixture manifest를 mode-600 aggregate report로 검증하며 provider·credential·broker·Paper 실행을 열지 않음. 실제 live capability registry·raw lake·Parquet replay는 아직 없음
- armed entry CLI는 free-form 종목·가격·시각·수량을 받지 않고 query-only watch SQLite에서 현재 직전 완료 정규장 1분봉에 결합된 30초 이내 ORB `setup` 후보 정확히 하나만 1주 요청으로 투영한 뒤 credential·운영 세션을 연다
- 모든 Alpaca Paper 운영 CLI는 잡힌 실행 예외의 클래스명만 stderr·보고서에 남기고 원문 계좌·broker·경로 정보를 버림
- bootstrap·readiness·recovery·entry·보호 OCO·safety 운영 report는 기존 파일을 포함해 atomic mode `600`으로 강제 교체
- 첫 정규장 smoke의 GET-only 준비, exact current ORB 후보 선택, armed entry, 보호 OCO, timeout 복구, staged EOD 평탄화와 최종 flat 대사를 하나의 운영 런북으로 고정
- 동일 공개 운영 세션 API의 fake broker E2E가 entry→체결 trade update→보호 OCO→staged EOD cancel/close→최종 flat broker/shadow 대사를 검증하며, 실제 Paper POST/DELETE는 계속 0건
- Alpaca SIP 단일 종목 완료 1분봉 provider bridge 구현. 정규장·canonical data URL·redirect 금지·단일 desired subscription을 HTTP 전에 검사하고, exact response body를 mode-600 append-only SQLite에 먼저 저장한 뒤 canonical Parquet·DuckDB replay identity와 restart offset을 M4 supervisor에 공급. fixture pagination·동일 분 retry·재시작·gap·휴장·다중종목·redirect E2E를 통과했으며 계좌·주문 import와 실제 외부 network 호출은 0건

## 다음 우선순위

1. 열린 NYSE 정규장과 mode-600 Alpaca data credential이 자연스럽게 동시에 맞을 때만 새 SIP bridge의 단일 종목 bounded GET smoke를 실행한다. exact raw page·canonical replay·runtime checkpoint를 대사하고 계좌·주문·Paper endpoint는 열지 않는다. 휴장에는 fixture E2E 결과만 유지한다.
2. 실제 read-only smoke 뒤 pagination·재시작 offset·provider gap의 장기 soak를 누적한다. 이 polling bridge를 websocket streaming이나 전체시장 coverage로 표현하지 않는다.
3. 현재 NYSE post-close와 mode-600 data credential·정렬된 bounded universe가 동시에 맞을 때만 US swing 일봉 source를 read-only로 한 번 수집한다. 그 뒤에만 동일 CLI로 signal/shadow forward evidence를 누적하며, Paper 계좌·주문은 열지 않는다.
4. current NYSE post-close source가 안전하게 축적된 뒤에만 새 US swing signal을 다음 정규장 전 local trial로 등록하고 terminal evidence를 누적한다. 표본·동일 위험 비교·승격 근거가 쌓이기 전에는 lane 권한을 바꾸지 않는다.
5. fixture E2E가 끝난 KR same-cycle orchestrator를 전체 품질 게이트와 수동 CLI QA로 확정한다. 현재 KST·자격증명·정상 endpoint 조건이 모두 맞을 때만 별도 bounded production same-cycle을 read-only로 실행하고, 아니면 provider를 억지로 열지 않는다.
6. 동일-cycle production coverage가 immutable evidence로 확정된 뒤에만 별도 manifest로 KR keyword Opportunity projection을 실행한다. source 실패를 성공이나 부분 complete로 축소하지 않으며, projection도 TradeSignal·국내 주문을 열지 않는다.
7. 새 코드 commit이 생긴 경우 다음 NYSE 개장 전에 clean checkout의 local-only experiment ledger bootstrap을 실행해 code-coupled strategy version을 append한다. 정규장 뒤 누락된 preregistration은 소급 생성하지 않고 read-only 관찰만 보존한다.
8. 열린 뉴욕 정규장에서 축소 entry 1건 → 즉시 보호 OCO → WSS·REST·Account Activities·원장 대사 → armed safety cancel/flatten → open order 0·position 0 최종 대사를 한 smoke로 검증
9. 실제 적격 ORB 세션마다 preregistered daily trial을 누적하고 terminal replay·실패·검열 운영 결과를 대사하되 열린 trial을 임의 terminal로 추정하지 않음
10. 추가 부분체결이 실제 발생할 때 staged 보호 OCO cancel → terminal 대사 → 다음 호출 replacement를 같은 축소 한도에서 검증하되 체결을 억지로 만들지 않음
11. equal-risk terminal trial·broker/shadow·DSR/PBO·parameter plateau·SIP 증거 계약이 모두 생긴 뒤에만 comparison·promotion Controller 단계를 별도 구현
12. 최소 두 executable lane champion 전에는 Portfolio Manager를 구현하지 않음
13. Milestone 3은 기존 bounded US·KR raw receipt를 새 계약의 object partition manifest로 투영하는 read-only 경로부터 시작하고, 그 뒤에만 Parquet canonical writer·DuckDB replay·correction/tombstone conformance를 추가

## 시작 전 확인

- `AGENTS.md`의 메모리·보안·paper-only 주문 경계를 지킨다.
- `docs/runtime_audit.md`의 인과성 결함과 수정 내역을 읽는다.
- 실시간 작업 전 `uv run pytest -q`를 실행한다.
- API 키·토큰을 프롬프트·로그·코드·리포트에 출력하지 않는다.
- KIS는 전체시장 백테스트 원천이 아니라 forward paper 시세원으로만 사용한다.
- `docs/runbooks/alpaca-paper-first-regular-session-smoke-ko.md`를 순서대로 따르고, nonzero 단계나 stale 후보를 우회하지 않는다.

## 새 작업용 요청문

```text
이 프로젝트의 README.md, CODEX_START_HERE.md, AGENTS.md와 docs/runtime_audit.md를 먼저 읽어줘.
현재 KR raw-first source cycle과 Single Writer Alpaca Paper·ORB 일일 shadow trial 운영 경계를 이어서 개발해줘.
Alpaca SIP 단일 종목 완료 분봉 polling bridge의 fixture E2E는 완료됐다. 실제 외부 GET은 열린 NYSE 정규장·mode-600 market-data credential·SIP entitlement가 모두 맞을 때만 bounded read-only smoke로 실행하고, 계좌·주문·Paper endpoint는 열지 마.
KR same-cycle orchestrator와 fixture E2E는 구현됐다. 다음에는 전체 품질 게이트와 수동 CLI QA를 마무리하고, 현재 KST·자격증명·정상 endpoint 조건이 모두 갖춰질 때만 bounded production same-cycle을 read-only로 수집해줘. 그 coverage가 확정된 뒤에는 별도 manifest로 KR keyword Opportunity projection을 실행하되 TradeSignal·국내 주문은 열지 마.
열린 정규장과 credential·current ORB 후보가 모두 갖춰진 경우에만 축소 Paper 수명주기 smoke를 실행하고, 하나라도 부족하면 broker mutation을 하지 마. 일일 trial은 exact preregistration과 terminal evidence를 유지하고 열린 trial을 추정으로 닫지 마.
```

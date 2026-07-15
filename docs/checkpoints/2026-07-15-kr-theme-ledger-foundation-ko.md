# KR Theme Ledger Foundation 체크포인트

## 범위

다중 시장 Research OS의 `kr_equities/opportunity_manager/theme_momentum` 세로 경로를 위한 첫 로컬 기반을 추가했다. 이 단계는 뉴스·공시·수급 촉매를 시점 가용 원문으로 보존하고, 후속 keyword/LLM 분류 결과가 원문 계보를 잃지 않도록 저장 계약을 고정한다.

실제 뉴스·DART·KIS 국내 API, LLM, 실시간 시세 또는 broker에는 연결하지 않았다. 한국 계좌·잔고·포지션·주문 경로는 없으며 미국 Alpaca Paper 실행 코드도 변경하지 않았다.

## 구현

### 촉매·coverage·분류 계약

- 촉매 source는 `news`, `dart`, `kis_ranking`, `volume_surge` 네 종류로 고정했다.
- source와 source record ID로 deterministic catalyst ID를 만들고 원문 SHA-256, source 주장 발행시각과 로컬 최초 관측시각을 분리했다.
- collection cycle은 네 source coverage를 정확히 한 번씩 canonical order로 가져야 하며 실패도 명시적으로 보존한다.
- KR 관련 종목은 6자리 symbol, 관계와 근거를 저장한다.
- 분류는 classifier kind/version, prompt version, run ID, 시각, 방향, confidence, 최소 evidence quote와 관련 종목을 보존한다.
- 같은 촉매의 재분류는 run별 새 immutable classification이며 과거 행을 덮어쓰지 않는다.

### Private append-only ledger

- `kr_catalysts`: exact raw BLOB과 checksum, deterministic source identity
- `kr_catalyst_observations`: cycle에서 촉매를 실제 관측한 관계
- `kr_collection_cycles`: exact source coverage와 완전성
- `kr_theme_classifications`: 원문 catalyst를 참조하는 버전형 분류 결과

모든 표의 UPDATE/DELETE는 SQLite trigger가 거부한다. DB와 writer lock은 mode `600`, writer는 nonblocking 단일 lease, reader는 `mode=ro`와 `query_only`다. reader는 model identity와 raw BLOB checksum을 다시 검증하며 반환 객체의 repr에도 raw byte를 포함하지 않는다.

동일 source identity와 동일 원문은 idempotent하다. 다른 cycle에서 다시 관측하면 원문을 복제하지 않고 observation만 추가한다. 동일 identity의 원문·metadata 변경, 관측 수와 coverage count 불일치, 존재하지 않거나 아직 관측되지 않은 촉매의 분류는 fail-closed한다.

### Local raw-first ingest

`run_kr_theme_ingest.py`는 명시적인 manifest, database와 output directory만 받는다. payload path는 manifest directory 안의 상대 regular file이어야 하고 traversal, symlink escape, missing/empty payload, duplicate source identity와 coverage count 불일치를 writer를 열기 전에 거부한다.

모든 payload byte를 먼저 읽고 hash·계약을 검증한 뒤 한 writer lease에서 원문, observation, cycle 순서로 append한다. 한국어 보고서에는 aggregate source 상태와 건수만 기록하고 제목·본문·종목·원문 hash를 노출하지 않는다.

## 검증

- 전체 pytest: `1042 passed`
- 새 KR contract/store/manifest/CLI focused 테스트: `26 passed`
- Ruff 전체: 통과
- basedpyright 전체: 오류 0, 경고 0
- `./run_kr_theme_ingest.py --help`: exit 0
- 존재하지 않는 manifest: DB 생성 전 exit 2
- synthetic happy path: 원문 2건, 관측 2건, 완전 cycle 1건
- 동일 fixture 재실행: 신규 원문 0건, 신규 관측 0건
- 생성 DB mode: `600`
- aggregate 보고서 raw 제목·종목·source ID·payload hash 비노출 확인
- 외부 network·LLM 호출: 0건
- broker·계좌·주문 mutation: 0건

synthetic fixture와 저장 계약 검증은 테마 분류 정확도, 시장 예측력 또는 수익성 증거가 아니다.

## 커밋

- `e50dfda feat: add KR theme evidence contracts`
- `fdb87b8 feat: add append-only KR theme ledger`
- `141d767 feat: ingest local KR catalyst manifests`
- `53ba0ad fix: keep KR catalyst payloads out of repr`

## 남은 Milestone 3

1. production read-only 뉴스·DART·KIS 국내 랭킹 수집 adapter
2. deterministic keyword baseline과 configured LLM 분류 adapter
3. 분류 안정성·keyword 대비 평가와 human audit sample
4. 저장 분류만 replay하는 theme freshness·strength·leader projection
5. `kr_equities/opportunity_manager/theme_momentum` Opportunity Snapshot 발행

KR VI·가격제한·투자경고·거래정지·동시호가·호가 freshness gate와 day shadow 전략은 Milestone 4다. 국내 execution은 설계상 계속 범위 밖이다.

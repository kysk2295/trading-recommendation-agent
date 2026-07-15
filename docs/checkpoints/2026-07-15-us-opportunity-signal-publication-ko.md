# US Opportunity·Conditional Signal 발행 체크포인트

## 범위

다중 시장 Research OS 설계의 첫 실제 세로 경로로 기존 KIS 미국주식 랭킹·NYSE 거래정지·시장위험 게이트·intraday 추천을 새 계약 계층에 연결했다. 기존 스캐너, SQLite 추천 원장, v1 추천 outbox와 Alpaca Paper 실행 경로는 변경하지 않았다.

이 단계의 KIS는 한국투자증권 API를 뜻하지만 대상 거래소는 `NAS`, `NYS`, `AMS`이며 시장 도메인은 `us_equities`다. 한국장 `kr_equities` theme manager와 KR day shadow는 다음 독립 vertical이며 아직 실행되지 않는다.

## 구현

### Opportunity projector

- 상승률·거래량 두 source와 NASDAQ·NYSE·AMEX 세 거래소의 정확한 6개 ranking group을 요구한다.
- 실패·누락·중복 group이 있으면 v2 opportunity를 발행하지 않는다.
- NYSE halt snapshot과 시장위험 screen이 opportunity 관측시각보다 미래이면 거부한다.
- 실제 선별 후보가 동일 discovery에 존재하는지 확인한다.
- 점수는 기존 `change_pct`만 사용하며 별도 confidence를 만들지 않는다.
- 가격·등락률·거래량·거래대금·volume/ADV·spread를 정렬된 feature로 남긴다.
- producer는 `kis-risk-screen-v1`, lane은 `us_equities/opportunity_manager/ranking_momentum`, 유효기간은 60초다.

### Conditional signal publisher

- exact opportunity 후보와 같은 종목의 `RecommendationState.SETUP`만 허용한다.
- recommendation이 opportunity 관측 이후이면서 그 유효구간 안에 있어야 한다.
- 실행 cycle의 `created_after` 이전 추천, 미래 추천, 발행 시점 기준 5분 이상 지난 추천은 제외한다.
- ORB, VWAP reclaim, HOD breakout, Gap-and-Go canonical ID를 기존 저장 strategy 이름에 명시적으로 대응한다.
- 신호 evidence는 immutable opportunity ID와 기존 recommendation ID를 참조한다.
- 발행 유효종료는 recommendation 이후 60초와 opportunity 만료 중 이른 시각이다.
- 발행 직전 현재 호가를 재조회하지 않으므로 모든 신호는 `conditional`, `quote_validation=None`이다.

### Append-only local outbox

- `opportunities.v1.jsonl`
- `trade-signals.v1.jsonl`
- `trade-signal-cards-ko/*.ko.md`

기존 JSONL을 문자열 검색하지 않고 각 행을 Pydantic 계약으로 다시 읽는다. 같은 ID와 같은 payload는 no-op, 같은 ID와 다른 payload·잘못된 JSON·다른 schema 객체는 fail-closed한다. 신호 ID에 경로 문자가 있어도 카드가 지정 디렉터리 밖에 생성되지 않도록 안전한 이름과 digest를 사용한다.

## 하위호환

- `paper_recommendations.sqlite3`, `recommendations_ko.md`, `recommendation_alerts.jsonl`, `recommendation_alerts_ko.md`는 기존 경로와 의미를 유지한다.
- 기존 KIS ranking CSV, request coverage, market-risk CSV, scan summary와 부분 모집단 exit code를 유지한다.
- broker client나 mutation adapter를 import하거나 호출하지 않는다.
- 실제 KIS/Alpaca 자격증명을 QA에 사용하지 않았고 외부 POST/DELETE는 0건이다.

## 검증

- 전체 pytest: `1016 passed`
- 새 projector/outbox/publication/integration 및 기존 adapter focused 회귀: 통과
- KIS watch·halt follow·ORB trial 관련 회귀: `22 passed`
- Ruff 전체: 통과
- basedpyright 전체: 오류 0, 경고 0
- 실제 `./run_kis_paper_scan.py --help`: exit 0
- 실제 `./run_kis_paper_scan.py --top 0`: 자격증명 로딩 전 exit 2
- fixture helper happy path: opportunity 1건, conditional signal 1건, 한국어 카드 1건
- 같은 fixture 재실행: 신규 signal 0건, JSONL 1행 유지, 기존 v1 outbox 내용 유지

fixture 결과는 계약·오케스트레이션 QA일 뿐 실제 시장의 현재 추천이나 수익성 증거가 아니다.

## 커밋

- `cd9ff26 feat: project KIS rankings to opportunities`
- `c6fe7f6 feat: add immutable contract outboxes`
- `d4b512d feat: publish conditional trade signals`
- `ea8f51f feat: emit KIS opportunity and signal contracts`
- `6a6036f fix: require exact ranked opportunity rows`

## 다음 단계

1. 발행 직전 fresh quote를 독립적으로 재조회하고 timestamp·spread·slippage를 검증하는 경계
2. quote 검증 전후 actionability를 구분하면서도 주문권한과 분리된 외부 delivery adapter
3. `kr_equities/opportunity_manager/theme_momentum`과 KR day shadow의 point-in-time theme evidence 수집
4. 같은 공통 검증 커널 위에 swing·systematic quant 실험 lane 추가

Portfolio/Allocation Manager는 최소 두 실행 lane champion이 생기기 전까지 주문과 배분 모두 구현하지 않는다.

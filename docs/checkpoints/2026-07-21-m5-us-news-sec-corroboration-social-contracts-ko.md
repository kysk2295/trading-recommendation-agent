# Milestone 5: US 뉴스·SEC 교차 claim + 소셜 계약 체크포인트

## 위치

설계 문서 Milestone 5 (`News·공시·소셜 evidence`) 진행 조각이다.

이미 완료된 M5 선행 작업:

- Alpaca news raw-first · coverage · opportunity evidence
- SEC EDGAR submissions/history · filing document · capability
- US news-catalyst Opportunity → shadow trial → Reviewer → day session scheduler
- research evidence read model (corroboration kernel)

이번 체크포인트는 그 위에 **typed claim extraction** 과 **소셜 evidence 사전 계약**을 얹는다.

## 구현

### 1) US 뉴스 attention claim

`trading_agent/us_news_research_extraction.py`

- 입력: `AlpacaNewsArticle` + raw `receipt_id` + symbol binding + `normalized_at`
- 출력: `CanonicalEventEnvelope` + `ResearchClaimExtraction`
- source: `alpaca/news`
- claim_key: `us.symbol.attention.{symbol}`
- claim_kind: `symbol.attention`
- stance: `reports` (방향·sentiment 추정 없음)
- fail-closed: 미포함 심볼, receipt 형식, 정규화 시각 역행

### 2) US SEC filing attention claim

`trading_agent/us_sec_filing_research_extraction.py`

- 입력: `SecFilingEvent` + raw `receipt_id` + **명시적 symbol binding** + `normalized_at`
- CIK만으로 ticker를 추정하지 않는다 (security-master 결합은 호출자 책임)
- source: `sec/edgar_submissions`
- 같은 claim_key/entity를 사용해 뉴스와 교차 가능
- form은 quality flag `form_{slug}` 로만 기록

### 3) 교차 검증

동일 `claim_key` + instrument entity + 서로 다른 source 두 건을
기존 `build_research_evidence_read_model`에 넣으면 `corroborated` 가 된다.

fixture: AAPL 뉴스 + 8-K → independent source 2, CORROBORATED.

### 4) 소셜 evidence 계약 (connector 전)

`trading_agent/social_evidence_models.py`

- `SocialPlatform`: `x`, `reddit`
- `SocialEntitlementContract`: 공식 API only, 무단 크롤 금지, shadow-only 운영모드
- raw text 저장 시 redistribution=`none` 강제
- `SocialEvidenceSnapshot`: order/lifecycle authority 항상 false
- **네트워크·실 connector·주문 경로 없음**

## 검증

```bash
uv run pytest tests/test_us_m5_research_evidence_extraction.py tests/test_social_evidence_models.py -q
# 6 passed
uv run ruff check trading_agent/us_news_research_extraction.py \
  trading_agent/us_sec_filing_research_extraction.py \
  trading_agent/social_evidence_models.py
uv run basedpyright trading_agent/us_news_research_extraction.py \
  trading_agent/us_sec_filing_research_extraction.py \
  trading_agent/social_evidence_models.py
```

- provider network: 0
- account/order mutation: 0

## Milestone 5 잔여

| 항목 | 상태 |
|---|---|
| SEC·DART·허용 뉴스 source | 대부분 완료 |
| entity·claim·burst·corroboration | kernel + KR + US news/SEC 추출 완료 |
| X·Reddit **계약** | 이번 체크포인트 |
| X·Reddit **공식 connector 수집기** | 미구현 (entitlement 확보 후) |
| social → shadow experiment 연결 | 미구현 |
| news-catalyst day service (launchd tick) | 코드/테스트 존재 (별도 서비스 체크포인트) |

## 다음 Milestone 순서 (설계 §17)

1. **M5 잔여**: official social connector (자격·API 계약 후) 또는 filing-document claim 결합
2. **M1 운영**: 열린 정규장 Paper armed smoke (조건 충족 시)
3. **M7**: KR live recommendation card (국내 주문 없음)
4. **M6**: market context / derivatives read-only (M5 소셜 수집과 병행 가능하나 우선순위 후)
5. M8–M10: comparison/promotion 표본 이후에만

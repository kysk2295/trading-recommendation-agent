# 한국 테마주 Shadow 연구 Lane 설계

- 상태: 제안 (사용자 승인 대기)
- 작성일: 2026-07-15
- lane 이름: `kr_theme_momentum`
- 실행 범위: 읽기 전용 촉매 수집 → LLM 테마 분류 → 규칙 기반 shadow 평가. **국내 주문 경로 없음**
- 데이터 범위: forward-only. 과거 뉴스 백테스트는 성과 증거로 사용하지 않음
- 실거래: 금지 (기존 AGENTS.md 경계 계승)

## 1. 결정 요약

국내 테마주 현상(뉴스·공시 촉매 → 테마 형성 → 관련주 동반 급등 → 순환)을 대상으로 하는 새 연구 lane을 추가한다. 기존 `intraday_momentum` lane과 같은 커널(인과성 계약, append-only 원장, 승격 상태기계, 다중검정 통제)을 재사용하되, 데이터 계층과 위험 게이트는 KR 시장 특수성에 맞춰 새로 만든다.

핵심 결정은 다음과 같다.

1. 구조는 4층으로 고정한다: 촉매 수집 → 테마 엔진 → 규칙 전략 → 기존 검증 커널.
2. LLM은 2층(테마 분류)에만 존재한다. 매매 판단·수량 산정·타이밍 결정에 LLM을 사용하지 않는다.
3. 분류 결과는 관찰 시각·모델 버전·프롬프트 버전과 함께 append-only 박제한다. replay는 저장된 분류만 사용한다.
4. 이 lane의 유일한 신뢰 가능 데이터는 forward로 쌓는 테마 관측 원장이다. 전략 검증보다 데이터 축적이 선행한다.
5. 전략 공간은 테마 초동이 아니라 초동 이후(첫 눌림, 대장주 확정 후 2파, 순환 후발)로 제한한다.
6. shadow 체결 모델은 상한가·VI·단일가 등 KR 체결 불가 상태를 처음부터 반영한다.

## 2. 배경과 근거

### 2.1 왜 이 lane인가

- 리테일의 구조적 엣지는 용량(capacity)이다. 국내 테마주는 기관이 규모 때문에 진입하지 못하는 대표적 저용량 시장이다.
- [Lopez-Lira & Tang (2023)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4412788)은 LLM의 뉴스 헤드라인 분류가 익일 수익률을 유의하게 예측하며, 효과가 **소형주와 악재 뉴스에서 가장 강함**을 보였다. 테마 신호가 저용량 영역에 남아 있다는 실증이다.
- 중국 concept stock(概念股) 문헌([Yin & Liu 2026](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6522518) 등)은 유사한 시장 구조(리테일 주도, 가격제한폭, 테마 순환)에서 컨셉 주도 거래를 학술적으로 다룬 선행 연구다.
- 국내 단타 고수의 매매 복기는 "왜 반대편이 돈을 잃는가"에 대한 현장 가설 소스다. 단 생존 편향·사후 서사 문제가 있으므로 직접 모방이 아니라 반증 가능한 규칙으로 번역해 검증한다.

### 2.2 기존 커널에서 재사용하는 것

- raw-first append-only 저장 (Alpaca `trade_updates` 원장 패턴)
- 관찰 시각 분리와 시점 인과성 계약 (`first_observed_at`, 완료 봉 규칙)
- fail-closed 사이클 감사, 부분 실패의 정직한 기록 (coverage CSV 패턴)
- 승격 상태기계 `IDEA → HISTORICAL → EXPERIMENTAL_PAPER → CHALLENGER → PAPER_CHAMPION ↔ SUSPENDED`
- 전역 experiment ledger와 DSR/PBO 시도 수 합산

## 3. 목표와 비목표

### 3.1 목표

- 국내 뉴스·공시·랭킹 촉매를 관찰 시각과 함께 무손실 축적한다.
- LLM 분류로 테마-종목 매핑, 테마 신선도·강도, 대장주 판정을 매일 기록한다.
- 축적된 관측 원장에서 반증 가능한 규칙 전략 가설을 등록하고 shadow로 검증한다.
- KR 시장 특수 상태(상한가·VI·단일가·거래정지)를 체결 모델과 위험 게이트에 반영한다.

### 3.2 비목표

- 국내 주문 제출 또는 국내 계좌 연결 (KIS는 계속 시세 조회 전용)
- 테마 초동(뉴스 후 수초~수분) 진입 — 폴링 기반 시스템의 속도로 불가능하며 시도하지 않는다
- LLM의 실시간 매매 판단 — 재현 불가능하므로 금지
- 과거 뉴스 아카이브 백테스트를 전략 성과 증거로 사용 — parametric look-ahead 오염
- 매매 칼럼·복기의 직접 모방 — 생존 편향 위이므로 번역·검증 대상으로만 사용

## 4. 외부 연구에서 채택한 원칙

- **LLM 뉴스 분류의 예측력**: [Lopez-Lira & Tang](https://arxiv.org/abs/2304.07619)의 호재/악재/무관 분류 프로토콜을 테마 분류로 확장한다.
- **Parametric look-ahead bias**: LLM 가중치 안의 미래 지식은 과거 데이터 분류를 오염시킨다([Look-Ahead-Bench](https://arxiv.org/pdf/2601.13770), [Oracle 논문](https://arxiv.org/pdf/2605.24564)). 따라서 forward-only 수집을 1급 원칙으로 한다.
- **Entity neutering**: 과거 데이터 예비 연구가 필요하면 종목명·날짜를 제거한 뒤 분류한다([Gao et al.](https://arxiv.org/pdf/2512.23847), [GPT 감성 look-ahead 평가](https://arxiv.org/html/2309.17322)). 그 결과도 진단용으로만 쓰고 승격 증거로 쓰지 않는다.
- **전파도(dissemination) 특징**: 뉴스가 얼마나 퍼졌는지가 예측력을 개선한다([FinGPT](https://arxiv.org/abs/2412.10823)). 동일 테마 기사 수·매체 수를 강도 특징으로 저장한다.

## 5. 비교한 대안

### 5.1 LLM 재량 트레이딩 (TradingAgents 방식) — 기각

LLM이 실시간으로 장을 보고 매매를 결정하는 구조. 같은 입력에 같은 신호가 재현되지 않아 replay parity·인접 파라미터 plateau·반증 감사가 모두 불가능하다. 이 프로젝트의 검증 체계와 원리적으로 충돌한다.

### 5.2 과거 뉴스 아카이브 백테스트 우선 — 기각

과거 테마의 열도를 재구성하기 어렵고, LLM이 학습으로 해당 테마의 미래를 이미 알고 있어 분류가 오염된다. 익명 헤드라인이 원본보다 in-sample 성과가 낮다는 실증이 오염의 존재를 증명한다.

### 5.3 키워드 사전 기반 테마 분류 (LLM 없이) — baseline으로 병행

키워드→관련주 테이블(예: [taes-k/stock_analysis](https://github.com/taes-k/stock_analysis) 방식)은 재현성이 완벽하지만 신규 테마·문맥 파악이 약하다. LLM 분류의 대조군 baseline으로 유지할 가치가 있다. LLM 분류가 키워드 사전 대비 우위임을 보이지 못하면 더 싼 쪽을 쓴다.

### 5.4 forward-only LLM 분류 + 규칙 전략 — 채택

분류는 LLM, 결정은 규칙, 검증은 기존 커널. 아래 아키텍처로 구현한다.

## 6. 논리 아키텍처

```text
[1층] 촉매 수집     뉴스·DART 공시·KIS 국내 랭킹·거래량 급증
                    → 원문 BLOB + observed_at + SHA-256 append-only
[2층] 테마 엔진     LLM 분류: 테마명·관련 종목·방향·강도 근거
                    → 분류 원장 append-only 박제 (모델·프롬프트 버전 포함)
                    → 테마 상태 projection: 신선도·강도·대장주
[3층] 규칙 전략     명시적 조건부 규칙만 (예: 신선 테마 + 대장주 + 첫 눌림 VWAP 지지)
                    → SignalIntent (기존 Strategy 계약 구현)
[4층] 검증 커널     KR 위험 게이트 → shadow 체결 → 이중 원장 → 승격 게이트
```

## 7. 데이터 계약

### 7.1 촉매 수집 (1층)

수집 대상과 최소 필드:

| 소스 | 내용 | 비고 |
|---|---|---|
| 뉴스 피드 | 제목·본문·매체·발행시각 | 소스별 어댑터, 실패는 coverage에 기록 |
| DART 공시 | 공시 유형·회사·원문 링크 | 공식 API |
| KIS 국내 랭킹 | 상승률·거래량 상위 (기존 US 패턴 재사용) | 읽기 전용 GET |
| 거래량 급증 감지 | 종목·시각·배율 | 랭킹 파생 |

모든 레코드는 다음을 저장한다: 원문 payload BLOB, `source`, `published_at`(소스 주장 시각), `observed_at`(우리가 실제로 본 시각), SHA-256. `published_at`과 `observed_at`을 절대 혼용하지 않는다 — 신호 가용 시각은 항상 `observed_at` 이후다.

### 7.2 테마 엔진 (2층) — LLM 분류 계약

입력: 촉매 레코드 1건. 출력 스키마(고정):

```text
theme_name        정규화된 테마 이름 (기존 테마 사전과 대조 후 신규/기존 판정)
related_symbols   관련 종목 코드 목록과 관련 근거 (직접 사업 / 지분 / 풍문)
direction         테마 관점 호재 / 악재 / 무관
confidence        분류 신뢰도 (낮으면 테마 상태에 반영하지 않고 보존만)
evidence_quote    원문에서 근거 문장 인용 (검증 가능성)
```

분류 원장 규칙:

- 분류 1건 = 원장 1행. `model_version`, `prompt_version`, `classified_at`을 함께 저장한다.
- UPDATE/DELETE 금지 trigger (실행 원장과 동일 패턴). 재분류는 새 행이며 기존 행을 대체하지 않는다.
- replay와 이후 연구는 저장된 분류만 사용한다. 과거 촉매를 새 모델로 재분류한 결과는 새 `classifier_version`의 병렬 원장으로 두고 기존 결과와 섞지 않는다 (평가기 버전 규칙과 동일).
- 분류 품질 자체를 검증한다: 동일 촉매 재분류 일치율(안정성), 주기적 인간 표본 감사, 키워드 baseline 대비 우위.

테마 상태 projection (분류 원장에서 재생성 가능해야 함):

- **신선도**: 테마 최초 관측 후 경과 시간
- **강도**: 관련 기사 수·매체 수(전파도), 관련 종목 중 랭킹 진입 수, 관련 종목 합산 거래대금
- **대장주**: 테마 내 당일 거래대금 1위 (규칙로 판정, LLM 판정 금지)

### 7.3 시장 데이터

KIS 국내 시세 API(기존 `~/.config/trading-agent/kis.env` 재사용, 읽기 전용)로 분봉·호가·VI/거래정지 상태를 조회한다. 미국 lane과 동일하게 완료 봉만 신호에 사용하고 관찰 시각을 기록한다.

## 8. KR 시장 특수 위험 게이트

미국 lane의 위험 게이트(halt·호가·spread)에 더해 다음을 차단 사유로 추가한다. 하나라도 확인 불가면 fail-closed.

- **가격제한폭**: 상한가 도달 종목은 매수 체결 불가로 처리한다. 상한가 근접(예: +27% 이상)은 신규 진입 차단.
- **VI(변동성완화장치)**: 정적·동적 VI 발동 중에는 신호 생성·shadow 체결을 모두 금지하고, 해제 후 첫 완료 봉부터 재개한다. VI 상태를 조회할 수 없으면 해당 종목은 차단.
- **단일가 매매 구간**: 연속 체결이 없는 구간은 분봉 전략 평가에서 제외한다.
- **거래정지·투자경고/위험/주의 지정**: 지정 종목은 신규 진입 차단. 지정 여부 조회 실패는 fail-closed.
- **shadow 체결 보수성**: 손절이 갭으로 관통되면 체결가를 `min(손절가, 봉 시가)`로 기록한다(US lane 스펙 §11.2 규칙을 처음부터 적용). 상한가 잔량 상태의 매수, 하한가 잔량 상태의 매도는 미체결로 남긴다.

## 9. 규칙 전략 공간 (Phase T3 이후)

초동 배제 원칙 아래 다음 계열만 가설 후보로 허용한다. 모두 기존 `IntradayStrategy` 계약을 구현하며, 테마 상태는 스캐너 특징으로만 주입된다.

- 신선 테마(48시간 이내) 대장주의 첫 눌림 매수 (VWAP reclaim의 테마 조건 버전)
- 대장주 확정 후 2파 돌파 (ORB 계열 + 테마 강도 필터)
- 테마 순환 후발주 — 대장주 상한가 이후 차순위 종목의 조건부 진입

매매 복기 번역 파이프라인: 칼럼·복기의 서사를 명시적 규칙 후보로 번역 → 반증 조건과 함께 IDEA 등록 → 위 검증 경로. 번역 출처(칼럼 링크·날짜)를 hypothesis 메타데이터에 남긴다. 규칙이 기각되면 "엣지가 규칙 밖(종목 선정·체결 감각)에 있다"는 정보로 다음 가설을 좁힌다.

## 10. 검증·승격 계약

- **적격 관측일**: 그날의 촉매 수집 coverage가 완전(소스별 성공, 실패 cycle 없음)하고 분류 원장과 수집 원장의 행 수가 대응할 때만 센다. 불완전한 날은 보존하되 게이트에 넣지 않는다.
- **증거 통화**: 이 lane은 시간 축적(적격일) + 횡단면(테마 수·종목 수)을 함께 센다. 소수 대형 테마에 표본이 몰리면 테마 단위 block bootstrap으로 의존성을 보존한다.
- **승격 기준**: 기존 커널 기준(최소 표본, 이중 원장 PF, 비용 후 양수, bootstrap CI, DSR/PBO, plateau)을 그대로 적용하되, 추가로 ① LLM 분류 안정성 기준 통과, ② 키워드 baseline 대비 우위, ③ VI·상한가 제외 체결 가능 표본 비율 공시를 요구한다.
- **다중검정**: 이 lane의 모든 가설·세그먼트 비교는 전역 experiment ledger에 합산한다. lane별 DSR을 따로 계산하지 않는다.

## 11. 하드 안전선

- KIS 국내 주문·잔고·계좌 엔드포인트를 코드에 추가하지 않는다. 이 lane의 실행 권한은 shadow까지다.
- LLM은 2층 분류만 수행한다. 3층 규칙·4층 게이트 코드는 LLM 호출을 포함할 수 없다.
- 분류 원장·촉매 원장은 append-only이며 UPDATE/DELETE를 DB trigger로 거부한다.
- 뉴스 원문 재배포 금지 — 원문 BLOB은 연구 내부용이며 외부 출력(리포트·카드)에는 인용 최소화.
- LLM API 키는 `~/.config/trading-agent/` 아래 별도 파일, mode 600. 프롬프트·응답 로그에 다른 자격증명이 섞이지 않게 한다.
- 과거 데이터 예비 연구는 entity neutering을 거쳐도 진단 전용이며, 승격 근거로 사용할 수 없음을 결과 파일에 명시한다.

## 12. 단계적 구현 순서

### Phase T0 — 촉매 수집기 (주문·전략·LLM 없음)

- 뉴스·DART·KIS 국내 랭킹 어댑터와 raw-first append-only 원장
- 소스별 coverage CSV와 fail-closed cycle 감사
- 실키 QA: 하루 수집 완전성, 재시작 중복 없음

### Phase T1 — 테마 분류와 관측 원장

- LLM 분류 계약 구현, 분류 원장, 테마 상태 projection
- 분류 안정성 측정(재분류 일치율)과 키워드 baseline
- 일일 테마 관측 리포트 (한국어, 성과 주장 없음)

### Phase T2 — 기초 통계 연구

- 테마 강도·신선도별 관련 종목의 익일 수익 분포 (기술 통계만, 전략 아님)
- 대장주 판정 규칙의 안정성 진단
- 이 결과는 가설 소스이며 성과 증거가 아니라고 명시

### Phase T3 — 규칙 전략 shadow

- KR 위험 게이트(§8) 구현과 회귀
- 복기 번역 파이프라인으로 첫 IDEA 등록
- KR shadow 체결 모델 (보수적 손절·VI·상한가 반영)과 일일 평가 연결

### Phase T4 — (보류) 국내 paper/실주문

- 이 스펙의 범위 밖이다. 별도 설계와 사용자 승인 없이 진행하지 않는다.

## 13. 완료 기준 (Phase T0–T1)

1. 촉매 수집이 관찰 시각·원문 BLOB·checksum과 함께 append-only로 쌓인다.
2. 소스 부분 실패가 성공으로 축약되지 않고 coverage에 남는다.
3. 동일 촉매 재수집이 중복 행을 만들지 않는다.
4. 분류 원장에서 테마 상태 projection을 언제든 재생성할 수 있다.
5. 분류 행마다 모델·프롬프트 버전이 있고, 다른 버전의 분류가 섞이지 않는다.
6. 재분류 일치율과 인간 감사 표본이 리포트에 나온다.
7. 어떤 출력물도 수익성·전략 우위를 주장하지 않는다.
8. 국내 주문 관련 코드가 존재하지 않는다.

## 14. 남은 운영 선택

핵심 설계를 바꾸지 않으므로 구현 중 기본값으로 시작하고 설정으로 노출할 수 있다.

- 뉴스 소스 목록과 폴링 주기
- 분류 LLM 모델과 비용 예산 (분류 volume이 크므로 소형 모델 + 표본 검증 구조 고려)
- 테마 신선도·강도 임계값의 초기 격자
- 일일 테마 리포트 전달 채널

이 항목들은 forward-only 원칙, append-only 원장, 주문 금지 경계를 약화할 수 없다.

## 15. 참고 문헌

- [Lopez-Lira & Tang — Can ChatGPT Forecast Stock Price Movements? (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4412788)
- [FinGPT — Dissemination-Aware Sentiment (arXiv)](https://arxiv.org/abs/2412.10823)
- [Gao, Jiang & Yan — A Test of Lookahead Bias in LLM Forecasts (arXiv)](https://arxiv.org/pdf/2512.23847)
- [Look-Ahead-Bench (arXiv)](https://arxiv.org/pdf/2601.13770)
- [Summoning the Oracle to Slay It — parametric look-ahead (arXiv)](https://arxiv.org/pdf/2605.24564)
- [Assessing Look-Ahead Bias in GPT Sentiment (arXiv)](https://arxiv.org/html/2309.17322)
- [Yin & Liu — Concept-Driven Trading in China's Stock Market (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6522518)
- [Prismatic — Concept Stock Clustering (arXiv)](https://arxiv.org/pdf/2402.08978)
- [키워드-관련주 매핑 선행 구현 (GitHub)](https://github.com/taes-k/stock_analysis)

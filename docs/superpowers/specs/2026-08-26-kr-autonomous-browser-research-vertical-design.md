# 한국시장 자율 브라우저 리서치·가상매매 수직 경로 설계

**상태:** 사용자 승인

**상위 설계:** `docs/superpowers/specs/2026-08-26-local-autonomous-trading-agent-design.md`

**제품 목표:** 24시간 실행되는 Autonomous Trading Supervisor가 전용 Chrome 프로필로 한국시장 SNS·커뮤니티·뉴스 흐름을 지속 조사하고, KIS 읽기 전용 시장데이터와 결합해 종목 가설과 진입·손절·목표를 만들며, 내부 가상체결·포지션 관리·결과 학습까지 하나의 재시작 가능한 ResearchTask로 수행한다.

## 1. 이번 릴리스의 결정

이번 릴리스는 다음 결정을 고정한다.

- KIS는 가격, 최신 완료봉, 호가, 거래대금과 관측 가능한 수급의 기준 데이터다.
- 기회 발견은 공시 중심이 아니라 SNS, 커뮤니티, 뉴스 확산 중심으로 한다.
- 공시, 거래소 공지와 기업 IR은 사실 확인이 가능한 경우에 사용하는 보조 증거다.
- Browser Research Agent는 전용 Chrome 프로필의 기존 로그인 세션을 사용한다.
- DOM 기반 검색과 읽기를 우선하고 동적 화면이나 로그인 화면은 Computer Use로 읽는다.
- 웹사이트 목록은 고정하지 않지만 브라우저 행동은 읽기 전용이다.
- 로그인 정보 입력, 게시, 댓글, 구매, 다운로드와 CAPTCHA 우회는 하지 않는다.
- 에이전트가 종목, 가설, 추가 조사, 진입 또는 관망을 자율적으로 결정한다.
- 가격 격자, 손절, 목표, 포지션 크기와 가상체결은 검증된 결정론적 도구가 확정한다.
- 공식 확인이 없는 SNS 가설도 현재 KRX 세션의 시장 반응이 확인되면 작은 가상 포지션으로 시험할 수 있다.
- 한국시장 주문, 계좌, 잔고 또는 실제 포지션 변경 경로는 만들지 않는다.
- 모든 추천, 기각, 가상체결과 학습 결과는 불변 이력과 원본 출처 계보를 가진다.

## 2. 범위와 비범위

### 2.1 포함 범위

- 지속형 Chrome 조사 작업의 생성, 재개, 차단과 다음 wake
- SNS·커뮤니티·뉴스 후보의 탐색, 정규화, 복제 군집화와 확산 분석
- URL, 게시 시각, 관찰 시각, 발췌, 화면 증거와 출처 신뢰도 저장
- KIS 읽기 전용 현재 세션 데이터와 브라우저 증거의 결합
- 자율적인 가설, 반대 근거, 조사 계획과 추천 또는 관망 결정
- 결정론적 진입·손절·목표·수량 계산
- 내부 가상체결, 포지션 추적과 동일 봉 손절 우선 청산
- Hermes 상태 변화 알림과 Dashboard 투영
- 출처·테마·시장 상태별 결과 기억과 Loop Engineer 입력 bundle
- Chrome, LLM 또는 프로세스 재시작 뒤 열린 Task 복구

### 2.2 비범위

- KIS, LS 또는 다른 한국 공급자의 주문·계좌·잔고·포지션 변경 호출
- Alpaca Paper를 포함한 미국시장 실행 경로
- 실제 자금 거래
- 게시물 작성, 좋아요, 댓글, 메시지 전송 또는 커뮤니티 참여
- 로그인 정보 저장, 자동 로그인 또는 CAPTCHA 우회
- Browser Research Agent가 웹 화면의 가격으로 체결을 확정하는 기능
- 이번 릴리스에서의 핵심 코드 자동수정과 모의운영 자동승격

완전한 Loop Engineer 자기수정기는 별도 릴리스다. 이번 릴리스는 즉시 사용할 수 있는 기억 학습과 Loop Engineer가 소비할 감사 가능한 evidence bundle까지 구현한다.

## 3. 설계 원칙

### 3.1 자율성과 계산 정확성의 분리

에이전트는 무엇을 조사하고 어떤 가설을 세우며 어떤 행동을 선택할지 결정한다. 가격 단위, 포지션 한도, 손절·목표 충돌과 가상체결은 코드 도구가 계산한다. 이 경계는 자율 판단을 one-shot 규칙기로 축소하지 않으면서 숫자 오류와 체결 왜곡을 막는다.

### 3.2 SNS는 발견 수단이고 KIS는 시장 사실의 기준이다

SNS의 속도와 다양성을 기회 발견에 사용하되 게시물 수를 사실의 수로 세지 않는다. 복사·재전파 관계를 군집화하고 독립 출처, 최초 시각, 계정 이력과 현재 시장 반응을 분리해 저장한다. 추천 시점의 가격과 거래대금은 최신 완료 KRX 봉과 현재 호가만 사용한다.

### 3.3 열린 목표는 조용히 종료하지 않는다

검색 결과 없음, Chrome 실패, 로그인 만료, LLM 실패와 `no-trade`는 자동 완료가 아니다. 다음 조사 조건이나 시간이 있으면 `waiting_event`, `waiting_time` 또는 `blocked`로 보존한다. 가설이 해결되거나 명시적으로 폐기된 경우에만 terminal 상태가 된다.

### 3.4 웹 콘텐츠는 명령이 아니라 불신 입력이다

웹페이지의 프롬프트, 다운로드 유도, 브라우저 설정 변경 요청과 외부 명령은 조사 대상 텍스트로만 취급한다. 도구 관찰은 원문 위치와 시각을 포함한 제한된 구조로 변환한 뒤 모델에 전달한다.

## 4. 구성요소

### 4.1 Autonomous Trading Supervisor

기존 Foundation의 durable task, memory, reasoning, tool runtime과 retry lineage를 사용한다. Supervisor는 시장 시간, 열린 KR 가설, 가상 포지션과 다음 wake를 기준으로 Browser Research Agent, Opportunity Agent, Trading Agent, Position Manager와 Critic 작업을 조정한다.

### 4.2 Browser Session Gateway

전용 Chrome 프로필과 실제 브라우저 세션의 생명주기를 소유한다.

- Chrome 실행 여부와 프로필 identity 확인
- 이미 로그인된 세션의 사용 가능 여부 확인
- DOM 읽기, 검색, 링크 이동과 탭 전환
- 동적 화면에서 Computer Use fallback
- 읽기 전용 행동 정책과 다운로드 차단
- 브라우저 충돌 뒤 동일 ResearchTask에서 세션 재개
- 로그인 만료, CAPTCHA, 차단과 GUI 세션 부재의 구분

Gateway는 브라우저 쿠키, 토큰, 전체 HTML, 원시 네트워크 헤더와 계정 식별자를 Task, 로그 또는 Dashboard에 저장하지 않는다.

### 4.3 Browser Research Agent

시장 변화와 열린 가설을 입력으로 받아 조사 계획을 스스로 갱신한다.

- X/Grok, 주식 커뮤니티, 종목 게시판, 공개 채널, 뉴스와 검색 결과 탐색
- 새 테마, 촉매, 종목명, 별칭과 관련 종목 추출
- 원 출처 추정과 복사·재전파 경로 조사
- 주장에 대한 지지·반대·미확인 상태 기록
- 다음 확인 대상, 검색어, URL과 wake 조건 생성

고정된 사이트별 스크레이퍼가 아니라 일반 브라우저 도구를 기본으로 사용한다. 반복 사용되는 안정된 출처는 이후 별도 읽기 어댑터로 최적화할 수 있지만 일반 Chrome 경로를 대체하지 않는다.

### 4.4 Social Evidence Store

각 관찰을 append-only 증거로 저장한다. 최소 필드는 다음과 같다.

```text
evidence_id
task_id
source_url
source_kind
source_identity_hash
published_at
first_observed_at
captured_at
title_or_author_label
bounded_excerpt
screenshot_digest
claim_entities
claim_summary
verification_state
repost_cluster_id
independent_source_cluster_id
content_digest
browser_session_receipt_id
```

`verification_state`는 `unverified_social`, `partially_corroborated`, `multi_source_corroborated`, `contradicted` 중 하나다. 원본 캡처와 변경 가능한 요약을 분리하고 요약의 각 버전은 사용한 evidence ID를 가진다.

### 4.5 Social Signal Normalizer

종목 코드, 회사명, 약칭, 인물, 제품과 테마 용어를 정규화한다. 동일 문구, 링크, 이미지 digest, 게시 시각과 출처 관계를 이용해 복사 게시물을 한 확산 군집으로 묶는다. 복사 게시물 100개를 독립 증거 100개로 세지 않는다.

출처 평가는 고정 평판 점수 하나가 아니라 다음 관찰 특징을 보존한다.

- 최초 게시와 재전파 시각
- 과거 가설의 확인·반증·시장 반응 결과
- 삭제·수정·반복 광고 패턴
- 독립 출처와의 일치 여부
- 특정 테마 또는 종목에 대한 전문성 이력
- 시장 반응보다 먼저 관찰됐는지 여부

### 4.6 KR Market Corroboration Tool

기존 KIS 읽기 전용 어댑터와 KRX session gate를 재사용한다.

- 현재 KRX 세션과 최신 완료봉 확인
- 데이터 날짜, freshness, 호가와 spread 확인
- 거래대금, 상대 거래량, 체결강도, VWAP 반응과 가격 구조
- 테마 동조, 대장주와 후발주 관계
- 관측 가능한 투자자·프로그램 수급 증거

세션 종료, 오래된 봉, 현재 날짜 불일치, 호가 또는 spread 부재는 신규 추천을 차단한다. 과거 봉은 지표 준비에만 사용하고 과거 시점 추천을 만들지 않는다.

### 4.7 Opportunity Agent

Browser Research Agent와 Market Corroboration Tool의 관찰을 결합해 다음을 만든다.

- 후보 종목과 테마
- 촉매 가설과 반대 가설
- 출처 독립성 및 확산 단계
- 시장 반응과 가설의 시간 순서
- 추가 조사 계획
- 진입 후보, 관찰 지속 또는 기각 제안

SNS 가설은 공식 확인이 없어도 현재 시장 반응과 시간 인과성이 있으면 실험 후보가 될 수 있다. 단일 출처를 다중 출처로 가장하거나 시장 반응 뒤에 발견한 게시물을 선행 신호로 기록할 수 없다.

### 4.8 Trading Agent와 Deterministic Trade Planner

Trading Agent는 종목, 방향, 가설, 무효화 조건과 행동을 선택한다. Trade Planner는 KRX 가격 단위와 현재 완료봉·호가를 사용해 다음 값을 확정한다.

- 추천 시각과 유효 시각
- 조건부 진입 가격 또는 범위
- 손절 가격
- 하나 이상의 목표 가격
- 포지션 크기
- 미체결 무효화 조건
- 가격과 데이터 provenance

`unverified_social` 가설은 별도 표시하고 검증된 가설보다 작은 가상 위험 예산만 허용한다. 추천은 시각, 진입, 손절, 목표, 크기, 근거, 반대 근거, 신뢰 상태와 immutable outcome history를 가져야 한다.

### 4.9 Critic Agent

가상 진입 전에 다음을 검사한다.

- SNS 주장과 실제 시장 반응의 시간 순서
- 복사 군집을 독립 증거로 잘못 센 오류
- 오래된 게시물의 재등장
- 광고·조작·확증편향 가능성
- 최신 완료봉, spread와 데이터 freshness
- 진입·손절·목표의 가격 격자와 위험 일관성
- 동일 종목·테마의 중복 가상 포지션
- 설명과 행동의 불일치

Critic은 보완 조사, 관망, 기각 또는 가상 진입 허용을 반환한다. 기각도 이유와 이후 결과를 남긴다.

### 4.10 Internal Virtual Execution과 Position Manager

기존 한국시장 shadow 원장과 체결 규칙을 재사용한다.

- `ARMED`와 실제 가상 `ACTIVE`를 구분
- 현재 완료봉 이후의 관측 가능한 가격만 체결에 사용
- 같은 봉에서 손절과 목표가 충돌하면 손절 우선
- 미체결, 만료, 손절, 목표, 시간 종료와 장 마감 청산 기록
- 테마 약화, 대장주 교체와 반증 증거를 포지션 Task에 전달
- 프로세스 재시작 뒤 열린 계획과 포지션 대사

어떤 경로도 KIS 주문, 계좌, 잔고 또는 실제 포지션 API를 호출하지 않는다.

### 4.11 Memory와 Outcome Learning

거래와 기각 결과는 다음 축으로 기억한다.

- 출처 identity와 독립 출처 군집
- 테마, 종목, 시장 상태와 시간대
- 가설의 검증 상태
- 5분, 15분, 30분, 장 마감 반응
- 진입, 미체결, 손절, 목표와 censored 결과
- 조사 누락, 데이터 실패, 판단 오류와 체결 오류

새 결과는 과거 원본을 수정하지 않고 새 memory version을 append한다. 이후 Agent는 관련 출처·테마 기억을 검색해 조사 우선순위와 신뢰 판단에 사용한다. 이 과정은 모델 재학습이나 수익성 보장을 의미하지 않는다.

### 4.12 Loop Engineer Evidence Bundle

다음 조건 중 하나가 반복되면 별도 개선 후보 bundle을 만든다.

- 같은 출처 유형 또는 확산 판단 오류가 세 번 이상 반복
- 복사 군집 또는 시간 순서 판정 오류가 반복
- 충분한 미래 가상매매 표본에서 기존 판단보다 지속적으로 악화
- Chrome UI, 사이트 계약 또는 입력 구조 변경으로 도구 실패 반복
- 현재 브라우저 도구로 중요한 가설을 검증할 수 없음

Bundle은 원본 evidence ID, task lineage, 판단, 결과, 실패 분류와 변경 가설을 포함한다. 이번 릴리스에서는 코드를 자동 수정하지 않는다.

## 5. 자율 도구 계약

Supervisor에 다음 범주의 typed tool을 제공한다.

```text
browser.session.status
browser.search
browser.open
browser.read
browser.follow
browser.capture
social.evidence.search
social.evidence.store
social.signal.normalize
kr.market.snapshot
kr.market.corroborate
kr.trade.plan
kr.virtual.execute
kr.position.reconcile
kr.outcome.observe
memory.search
memory.write
delivery.publish
dashboard.project
```

각 도구 호출은 현재 task ID와 root source evidence ID에 결속된다. 도구는 bounded observation만 반환하고 임의 shell, 임의 파일 쓰기, 브라우저 자격증명 추출 또는 공급자 mutation을 허용하지 않는다. Tool Runtime의 worker-module authority와 실행 예산을 통과하지 못하면 네트워크나 브라우저 행동 전에 거부한다.

## 6. 장중 데이터 흐름

```text
30초 Supervisor heartbeat
→ 현재 KRX 세션·열린 Task·가상 포지션 확인
→ SNS/커뮤니티/뉴스 변화 또는 시장 이상 감지
→ Browser ResearchTask 생성 또는 재개
→ Chrome 다단계 조사와 증거 저장
→ 복제 군집·독립 출처·시간 순서 정규화
→ KIS 현재 세션 데이터 결합
→ Opportunity 가설과 반대 가설
→ Trading Agent 행동 선택
→ Deterministic Trade Planner
→ Critic 검토
→ 추천·관망·기각
→ 내부 가상체결과 Position Manager wake
→ Hermes·Dashboard 상태 변화 투영
→ 결과 관찰과 memory append
```

중요 후보는 새 증거 또는 시장 변화가 있는 동안 최대 2분 이내에 재조사한다. 변화가 없는 페이지를 무한 새로고침하지 않는다. 사이트별 속도 제한, 오류 응답과 브라우저 상태를 반영해 다음 wake를 예약한다.

## 7. 시장별 운영 리듬

### 7.1 장전

- 밤사이 SNS·뉴스·해외 연결 테마 조사
- 반복 출처와 새로운 출처 분리
- 예상 테마·대장주·반대 시나리오 작성
- 장 시작 뒤 확인할 KIS 조건과 검색 wake 등록

### 7.2 장중

- 30초 Supervisor heartbeat
- 중요 후보 최대 2분 이내 재조사
- 새 게시물, 확산 급증, 대장주 교체, 거래대금 변화에 event wake
- 추천, 관망, 기각과 가상 포지션 상태 변화 알림

### 7.3 장 마감

- 모든 추천과 가상 포지션 terminal 또는 censored 처리
- 출처·테마·시장상태별 결과 기억 append
- 성공뿐 아니라 기각, 미체결, 손절과 조사 실패 평가
- 반복 실패의 Loop Engineer evidence bundle 생성

### 7.4 휴장

- 열린 브라우저 연구 과제 정리
- 출처 이력과 반증 조사
- 다음 세션 검색 계획과 wake 생성
- 장중 서비스와 충돌하지 않는 bounded 연구만 실행

## 8. 실패 처리

### 8.1 Chrome 또는 GUI 실패

- Chrome 종료는 기존 Task를 유지한 채 세션 재시작 작업을 만든다.
- 로그인 만료, CAPTCHA, GUI 세션 부재와 사이트 차단을 구분한다.
- 사람 조치가 필요한 경우 URL이나 비밀정보 대신 필요한 한 단계만 알린다.
- 복구 전까지 해당 출처를 `blocked`로 두되 다른 출처와 KIS 경로는 계속 실행한다.

### 8.2 SNS 품질 실패

- 삭제·수정 게시물은 새 관찰로 append하고 과거 캡처를 수정하지 않는다.
- 동일 내용의 재게시를 새 독립 증거로 세지 않는다.
- 오래된 게시물은 현재 확산의 대상일 수 있지만 최초 촉매로 기록하지 않는다.
- 광고·조작 의심은 별도 이유 코드와 함께 신뢰도를 낮춘다.

### 8.3 데이터 실패

- KIS 세션, freshness, 현재 날짜, 호가 또는 spread가 불완전하면 신규 추천을 차단한다.
- 브라우저 증거만으로 가격·체결을 추정하지 않는다.
- 데이터 복구 event에 열린 Task를 다시 깨운다.

### 8.4 LLM 실패

- 원본 목표, 증거, 마지막 성공 도구 관찰과 다음 계획을 보존한다.
- 예산 소진이나 인증 실패는 다음 wake 또는 사용자 인증 요청으로 전환한다.
- 같은 실패 요청을 무한 반복하지 않는다.

### 8.5 프로세스 재시작

- Task DB, memory DB, social evidence store와 가상 포지션 원장을 다시 연다.
- 미적용 결정과 열린 브라우저 조사 작업을 중복 호출 없이 재개한다.
- 현재 가상 포지션을 먼저 대사한 뒤 신규 기회 조사를 재개한다.

## 9. Hermes와 Dashboard

사용자 표면은 다음을 실시간으로 보여준다.

- 현재 Browser Research Agent의 목표, 단계, 마지막 행동과 다음 wake
- 조사 중인 테마·종목과 방문 출처의 redacted label
- SNS 확산 속도, 복사 군집과 독립 출처 수
- 주장 검증 상태와 KIS 시장 반응
- 가설, 반대 근거, Critic 판정
- 추천 시각, 진입, 손절, 목표, 크기와 무효화 조건
- 가상체결, 포지션, 실현·미실현 결과
- 기각, 실패, 재시도 lineage와 Chrome 차단 이유
- 새로 append된 출처·테마·전략 기억
- Loop Engineer 후보 bundle과 트리거 근거

Hermes는 상태가 바뀔 때만 알린다. 같은 URL, 같은 content digest, 같은 완료봉과 같은 판단을 반복 전송하지 않는다. Dashboard는 원시 쿠키, 토큰, 전체 게시물 본문, 계정 식별자와 임의 브라우저 로그를 표시하지 않는다.

## 10. 검증 전략

### 10.1 단위·계약 테스트

- Browser tool의 읽기 전용 행동과 credential·download·mutation 거부
- bounded excerpt, URL, published/observed 시각과 content digest
- 복사 게시물 군집화와 독립 출처 계산
- 오래된 재게시와 시장 반응 뒤 발견된 게시물의 인과성 차단
- SNS 검증 상태 전이와 반증 append
- KRX current-session latest-completed-bar gate
- 진입·손절·목표 가격 격자와 작은 미확인 가설 위험 예산
- 같은 봉 손절·목표 충돌 시 손절 우선
- 추천, 관망, 기각과 가상체결의 immutable outcome history
- restart replay, 다음 wake와 root source lineage
- Hermes deduplication과 Dashboard redaction

### 10.2 통합 테스트

고정된 브라우저 snapshot fixture와 KIS fixture로 다음 전체 흐름을 검증한다.

```text
새 SNS 테마
→ Browser ResearchTask
→ 복수 페이지 조사
→ 독립 출처 군집
→ KIS 시장 확인
→ 계획과 Critic
→ 작은 가상 진입
→ 손절 또는 목표
→ memory와 사용자 표면
```

반대 경로로 오래된 게시물, 복사 군집뿐인 확산, stale KIS 봉, missing spread, Chrome 로그인 만료와 LLM 실패가 추천·가상체결 없이 정확한 대기 또는 차단 상태를 남기는지 검증한다.

### 10.3 실제 브라우저 수동 QA

전용 Chrome 프로필에서 공개 페이지와 이미 로그인된 X/Grok·커뮤니티 페이지를 읽는다. 실제 화면에서 검색, 링크 이동, 동적 화면 fallback, 증거 캡처, Task 재개와 비밀정보 redaction을 관찰한다. 게시·댓글·다운로드·로그인 입력은 실행하지 않는다.

### 10.4 운영 검증

- launchd 아래 Chrome gateway와 Supervisor 재시작 복구
- 열린 KRX 세션에서 SNS 발견부터 추천 또는 명시적 관망까지 2분 내 상태 전이
- 가상 포지션의 장중 stop/target과 장 마감 terminal 대사
- Hermes 상태 변화 알림과 Dashboard 동일 lineage 확인
- 최소 한 개 실제 Chrome 조사 Task의 프로세스 재시작 후 복구
- KIS·LS mutation 0건과 한국 실제 주문 경로 부재 확인
- Alpaca 거래 호출 0건
- 비밀정보 노출 0건

## 11. 완료 기준

이번 릴리스는 다음 조건이 모두 관찰돼야 완료다.

1. 새 SNS·커뮤니티 테마가 durable KR ResearchTask를 만든다.
2. Agent가 Chrome에서 둘 이상의 관련 페이지를 자율적으로 조사한다.
3. 각 증거가 URL, 게시·관찰 시각, bounded excerpt, digest와 출처 군집을 가진다.
4. KIS 현재 세션 데이터가 가설과 결합된다.
5. Agent가 추천 또는 명시적 관망·기각을 제출한다.
6. 추천은 시각, 진입, 손절, 목표, 크기, 근거와 반대 근거를 가진다.
7. 미확인 SNS 가설은 별도 표시된 작은 가상 위험 예산만 사용한다.
8. 내부 가상체결과 Position Manager가 terminal 또는 censored outcome을 남긴다.
9. Hermes와 Dashboard가 같은 immutable lineage를 보여준다.
10. Chrome 또는 Supervisor 재시작 뒤 같은 Task와 포지션이 복구된다.
11. 출처·테마·시장상태별 결과 memory와 Loop Engineer bundle이 append된다.
12. KIS·LS 주문/계좌 mutation과 한국 실제 주문 경로가 존재하지 않는다.
13. synthetic, replay 또는 shadow 결과를 실제 수익성으로 표현하지 않는다.

## 12. 구현 순서

구현 계획은 다음 수직 순서를 따른다.

1. Browser session과 social evidence 경계
2. Browser Research Agent 도구와 durable Task 연결
3. Social signal 정규화·복제 군집·시간 인과성
4. KIS current-session corroboration 도구
5. Opportunity·Trading·Critic 다단계 루프
6. Deterministic Trade Planner와 내부 가상체결 연결
7. Position Manager, outcome memory와 Loop Engineer bundle
8. Hermes·Dashboard projection
9. launchd·Chrome restart 복구와 실제 브라우저 운영 검증

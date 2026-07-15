# Hermes OS 연동 설계 — 스케줄·배달·브리핑·주간 연구

- 상태: 제안 (사용자 승인 대기)
- 작성일: 2026-07-15
- 연동 대상: [Hermes OS](../../..) — 사용자의 개인 에이전트 OS (Railway UI + Mac mini runtime + Telegram/LLM-Wiki 커넥터)
- 원칙: Hermes는 이 프로젝트의 **바깥**에서 시동·배달·보고·연구 보조만 담당한다. 원장·주문·검증 경계 안으로는 절대 들어오지 않는다

## 1. 결정 요약

trading-recommendation-agent의 남은 4개 운영 갭을 Hermes OS의 기존 능력으로 채운다.

| 트레이딩 시스템의 갭 | Hermes의 기존 능력 | 연동 형태 |
|---|---|---|
| 매일 watch 시동 (루프 3의 마지막 조각) | Mac mini 상시 runtime + scheduler + launchd 생존 | Hermes 스케줄이 거래일마다 watch CLI 실행 |
| 실시간 알림 배달 | Telegram bot 커넥터 + 모바일 웹 | Hermes messenger가 alert outbox를 읽기 전용 폴링해 전송 |
| 일일 브리핑 | LLM-Wiki write-back + 시각적 result brief | 장후 산출물을 위키 브리프·아침 카드로 변환 |
| 주간 가설 생성 (루프 4) | Codex 기반 no-approval agent run | 주 1회 Researcher run이 실패 원장을 읽고 가설 초안 작성 |

핵심 결정:

1. Hermes는 이 저장소의 어떤 SQLite·JSONL 원장에도 **직접 쓰지 않는다**. 쓰기는 항상 이 저장소의 기존 CLI가 자기 Writer lease로 수행한다.
2. Hermes가 실행할 수 있는 명령은 **고정 allowlist**로 제한한다. 임의 인자 조합·임의 셸 실행을 허용하지 않는다.
3. `PaperMutationArm`을 포함한 주문 권한 표면은 Hermes UI·API·agent run 어디에도 노출하지 않는다.
4. 자격증명(KIS·Alpaca·LLM)은 지금처럼 runtime 머신 로컬의 mode 600 파일에만 있다. Hermes/Railway 환경변수로 복사하지 않는다.
5. 운영 본체(이 저장소 + watch)는 Mac mini로 이전해 Hermes runtime과 같은 머신에서 24/7 운영한다. 노트북 사본은 개발 전용이다.

## 2. 배경

- 세션 내부 자동화는 완성됐다: watch 하나가 trial 사전등록 → 정규장 감시 → metrics → 일일 기록 → adaptive → snapshot → Reviewer → trial 확정을 all-or-none으로 연쇄한다.
- 그러나 저장소에는 OS 수준 스케줄러가 없어 매 거래일 watch 시동이 수동이고, 미국 정규장(KST 22:30~05:00) 동안 기계가 깨어 있어야 한다.
- 추천 카드 outbox는 "외부 메시지 어댑터는 읽기 전용으로 소비해야 한다"고 설계되어 있으나 실제 어댑터가 미연결이다.
- Hermes OS는 이미 Mac mini 상시 runtime, launchd 생존, scheduler, Telegram bot, LLM-Wiki write-back, Codex agent run을 갖고 있다.

## 3. 목표와 비목표

### 3.1 목표

- 사람이 버튼을 누르지 않아도 매 거래일 watch가 돌고, 결과가 아침에 도착한다.
- 신규 추천이 생기면 수 분 내 Telegram으로 종목·조건부 진입가·손절·목표·**증거 등급**이 배달된다.
- 장후 산출물(적격 여부·성과·Reviewer 권고·trial 결과)이 LLM-Wiki 브리프와 모바일 카드로 요약된다.
- 주 1회 Hermes agent run이 실패 원장 기반 가설 초안을 만들어 사람 승인 대기열에 올린다.

### 3.2 비목표

- Hermes를 통한 주문 제출·arm·kill switch 조작 — 영구 금지
- Hermes UI에서 원장·전략 상태를 수정하는 기능
- 알림을 근거로 한 자동 매매 — 알림은 사람에게 가는 정보다
- Telegram 명령으로 전략 파라미터를 바꾸는 원격 제어

## 4. 아키텍처

```text
┌─ Mac mini ──────────────────────────────────────────────────┐
│                                                              │
│  [trading-recommendation-agent]   (독립 저장소, 기존 그대로)      │
│    watch → 원장·outbox·보고서        Single Writer 불변          │
│        ▲ CLI 실행(allowlist)          │ 파일 읽기 전용            │
│        │                             ▼                        │
│  [Hermes Runtime]                                             │
│    ① Scheduler   거래일 21:30 KST watch 시동, 휴장일은 watch가     │
│                  스스로 fail-closed 종료                        │
│    ② Runs        watch PID·종료코드 감시, 비정상 종료 시 즉시 알림   │
│    ③ Messenger   outbox 폴링(30s) → Telegram 전송               │
│    ④ Briefer     장후 산출물 → LLM-Wiki 브리프 + 아침 카드         │
│    ⑤ Researcher  주 1회 agent run: 실패 원장 → 가설 초안 PR       │
└──────────────────────────────────────────────────────────────┘
        ▲ HERMES_RUNTIME_URL (기존 터널·토큰)
[Railway UI / Telegram]  ← 폰에서 알림 수신·브리핑 열람·연구 태스크 승인
```

### 4.1 경계 계약 (불변)

- **쓰기 방향은 한쪽뿐**: Hermes → trading은 프로세스 시동만, trading → Hermes는 파일 읽기만.
- **명령 allowlist**: Hermes 스케줄·run이 실행할 수 있는 것은 사전 등록된 정확한 명령 문자열(README의 canonical watch 명령, 브리핑 리포터, 주간 연구 스크립트)뿐이다. 인자 치환은 날짜·출력 경로 같은 화이트리스트 필드만 허용한다.
- **읽기 전용 소비 지점**: `recommendation_alerts.jsonl`(및 outbox SQLite), `adaptive_evaluation_ko.md`, `daily_research_ledger.jsonl`, review ledger의 query-only reader, `watch_cycles.csv`. Messenger·Briefer는 이 파일들만 연다.
- **중복 발송 방지**: outbox가 추천 ID 기본키로 최초 1회를 보장하므로, Messenger는 자체 전송 원장(`hermes_delivery_ledger`)에 전송한 추천 ID를 append-only 기록하고 재시작 시 미전송분만 보낸다. 5분 이내 생성분만 신규 발송하는 기존 지연 발송 차단 규칙을 계승한다.
- **비밀 격리**: Messenger가 만드는 메시지에는 계좌 식별자·키가 들어갈 수 없다(원본 카드가 이미 redact됨). `HERMES_TELEGRAM_BOT_TOKEN`은 Hermes 쪽 비밀이고 trading env 파일과 섞지 않는다.

### 4.2 알림 메시지 계약

Telegram 카드는 outbox의 구조화 JSON에 전역 실험 원장의 현재 lifecycle 상태를 **읽기 전용으로** 결합해 만든다.

```text
🔔 [DAY · ORB v1 · EXPERIMENTAL_SHADOW]
ABCD — 12.48 돌파 시 조건부 진입
손절 12.28 · 목표 12.68 (1R) / 12.88 (2R)
근거: ORB 5분 상단 돌파, RVOL 2.1, 갭 +6.2%
증거: 적격 23/60일 · 완료 41거래 · 검증 미완료
이 카드는 paper 전진검증 기록이며 투자 권유가 아니다.
```

등급 표기와 마지막 면책 문구는 생략할 수 없다. lifecycle 상태를 읽지 못하면 등급을 `미확인`으로 표기하고 발송은 유지한다.

### 4.3 일일 브리핑 계약

장후 연쇄가 끝난 뒤(또는 매일 07:30 KST 중 늦은 쪽) Briefer가 다음을 한 장으로 요약한다.

- 어제 세션: 적격 여부와 사유, cycle 성공률, 신규 추천 수
- 전략별: 누적 적격일·완료 거래·현재 lifecycle 상태·adaptive 판정(collecting/diagnose 등)
- Reviewer 권고와 trial 확정 결과(completed/censored/failed)
- 운영 incident (KIS 재시도·실패 cycle)

출력: LLM-Wiki 페이지 1개(append) + Hermes 아침 카드 + Telegram 요약 1건. 성과 수치에는 항상 "검증 미완료" 단서가 붙는다.

### 4.4 주간 Researcher run 계약

- 주 1회(토요일) Hermes가 Codex agent run을 시작한다. 입력은 이 저장소의 읽기 전용 산출물(실패 원장·cohort 분해·Reviewer 권고)이다.
- 산출물은 반증 조건이 포함된 가설 초안 최대 3개의 **문서 PR**(또는 위키 초안)이다. 전역 실험 원장 등록은 사람이 초안을 승인한 뒤 기존 CLI로 수행한다.
- agent run은 원장 등록·전략 활성화·코드 병합 권한이 없다. challenger 활성화 주 1개 상한은 사람 승인 단계에서 집행한다.

## 5. 비교한 대안

- **launchd 단독 (Hermes 없이)** — 시동 문제만 풀리고 배달·브리핑·연구 run은 별도 구축이 필요하다. Hermes가 이미 그 셋을 갖고 있으므로 중복 투자다. 다만 Hermes runtime 장애 시 fallback으로 Mac mini launchd 직접 등록을 2차 안전망으로 둘 수 있다.
- **알림을 trading 저장소 안에 내장 (Telegram 코드 추가)** — 배달 실패 재시도·토큰 관리가 연구 저장소에 들어와 경계가 흐려진다. outbox 설계 의도(외부 어댑터의 읽기 전용 소비)와도 어긋난다. 기각.
- **Hermes에 주문 권한까지 위임** — "폰에서 승인하면 진입" 형태. 편리하지만 승인 게이트의 5초 신선도·current-epoch 계약과 양립 불가능하고, 원격 표면에 mutation 권한을 노출한다. 기각. 주문은 계속 이 저장소의 운영 세션 안에서만.

## 6. 단계적 구현 순서

### Phase H1 — 시동과 감시 (루프 3 완성)

- trading 저장소를 Mac mini에 clone하고 자격증명 파일(mode 600) 배치
- Hermes 스케줄에 canonical watch 명령 등록 (거래일 21:30 KST, README의 lane·experiment-ledger 전체 인자 포함)
- Runs에서 watch 종료코드 감시, 비정상 종료·적격 실패 시 Telegram 즉시 통보
- 완료 기준: 사람 개입 없이 2거래일 연속 watch 완주와 종료코드 통보

### Phase H2 — 알림 배달

- Messenger: outbox 폴링 → 증거 등급 결합 → Telegram 전송, 자체 전송 원장으로 멱등
- 완료 기준: 실거래일 신규 추천이 생성 후 5분 내 폰에 도착, 재시작 후 중복 발송 0건

### Phase H3 — 일일 브리핑

- Briefer: 장후 산출물 → LLM-Wiki + 아침 카드
- 완료 기준: 아침 카드에서 어제의 적격 여부·전략별 증거 누적을 30초 안에 파악 가능

### Phase H4 — 주간 Researcher run

- 실패 표본이 최소 2주 쌓인 뒤 시작. 가설 초안 → 사람 승인 → 기존 CLI 등록
- 완료 기준: 승인된 가설 1개가 전역 원장에 IDEA로 등록되는 전체 경로 1회 완주

### Phase H5 — KR 테마 수집기 편입

- KR 테마 lane T0 수집기(별도 스펙)를 같은 스케줄 체계에 등록 (국내장 시간대, 낮)

## 7. 완료 기준 (전체)

1. 매 거래일 watch가 사람 없이 시동·완주하고 실패는 폰으로 즉시 통보된다.
2. 신규 추천이 증거 등급·면책 문구와 함께 5분 내 Telegram에 도착한다.
3. Hermes 경로 어디에도 원장 쓰기·주문 권한·자격증명 노출이 없다.
4. Hermes 전체가 꺼져도 trading 시스템의 검증 무결성은 영향받지 않는다 (배달·브리핑만 멈춤).
5. 주간 연구 run의 산출물이 사람 승인 없이 원장·코드에 반영되는 경로가 존재하지 않는다.

## 8. 남은 운영 선택

- watch 시동 시각(기본 21:30 KST)과 폴링 주기(기본 30초)
- Telegram 외 추가 채널(Hermes 모바일 push)
- Mac mini 이전 시점 — H1 전 필수이나, 이전까지는 노트북 launchd로 임시 운영 가능
- 브리핑 위키 경로와 카드 형식

이 선택들은 §4.1 경계 계약과 §3.2 비목표를 약화할 수 없다.

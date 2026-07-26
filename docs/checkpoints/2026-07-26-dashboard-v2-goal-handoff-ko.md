# Dashboard v2 Goal 인계

## 승인 상태

- 사용자는 2026-07-26 `Ember Operations Workstation` 방향을 승인했다.
- 다음 작업은 새 Codex 세션의 Goal로 시작한다.
- 현재 dashboard v1 배포는 유지하며 v2를 점진적으로 배포한다.

## 작업 경계

- worktree:
  `/Users/goyunseo/work/trading-recommendation-agent/.worktrees/integration-20260723`
- branch: `codex/integration-20260723`
- dirty 원본 checkout은 수정, 정리, stash, reset하지 않는다.
- 실제 자금 거래는 영구 금지한다.
- KIS, LS와 Alpaca Paper 외 provider의 mutation은 금지한다.
- Alpaca 주문은 exact Paper base URL과 기존 arm/risk/reconcile gate를 모두 통과할 때만
  허용한다.

## Goal

전체 Quant Research OS를 Railway dashboard v2에 실제 데이터, 증거와 명령 경로로
시각화하고 운영 가능하게 완성한다.

Command Center를 기본 화면으로 하고 다음 workspace를 구현한다.

1. Command Center
2. Overview
3. Markets
4. Data Sources
5. Research
6. Strategies
7. Derivatives
8. Paper
9. System

각 workspace는 실제 append-only 원장의 redacted read-only projection을 사용한다.
데이터가 없으면 가짜 카드나 수치를 만들지 않고 `unavailable` 또는 `blocked`와 실제
차단 근거를 표시한다.

## 시각 방향

참조:

- 사용자 첨부 Fintrixty dark finance dashboard
- Behance `Crypto AI Trading Bot`
  <https://www.behance.net/gallery/210871221/Crypto-AI-Trading-Bot>
- Pinterest finance dashboard
  <https://kr.pinterest.com/pin/338755203244751485/>

채택:

- carbon-black 고정 app shell
- ember orange 단일 accent
- green, amber, red는 실제 semantic state에만 사용
- `Pretendard Variable`과 `IBM Plex Mono`
- desktop fixed sidebar, fixed context rail, workspace-owned scroll body
- compact asymmetric financial bento와 실제 table/chart 조합
- 모든 metric에서 source부터 Reviewer 또는 Paper 결과까지 여는 Evidence Trace drawer
- `DESIGN_VARIANCE: 5`
- `MOTION_INTENSITY: 3`
- `VISUAL_DENSITY: 9`

금지:

- AI purple, neon glow, glassmorphism
- 같은 크기 카드 반복
- 장식용 status dot과 fake precision
- card 안의 card 중첩
- 의미 없는 perpetual animation
- 레퍼런스 logo, 문구와 상표 자산 복제

Taste Skill은 dashboard layout template로 사용하지 않고 anti-slop, token discipline,
state completeness와 accessibility 규율로만 적용한다.

## 실제 제품 범위

### Command Center

- agent별 실제 지속 대화
- 로컬 mode-600 agent/session binding
- Hermes `--resume` 사용
- Railway에는 Hermes session ID나 로컬 식별자를 전송하지 않음
- 명시적 사용자 submit 한 건당 model 실행 한 번
- 자동 유료 재시도 없음

### Data Sources

- FRED/ALFRED, Treasury, CFTC, OpenDART, KIS, LS, Alpaca를 포함한 capability registry
- source class, market domain, entitlement, freshness, coverage, terminal receipt와 blocker
- credential value, account identity, raw header와 local path는 projection 금지

### Research와 Strategies

- research source, paper, hypothesis card와 queue
- causal dataset와 exact SHA
- strategy lane/version/trial
- walk-forward, overfit diagnostic, Reviewer와 lifecycle
- champion과 Allocation Manager 잠금 근거

### Derivatives

- option chain, IV, skew, term structure
- futures security master, roll window, CFTC positioning context
- current licensed quote가 없으면 research-only 또는 unavailable

### Paper

- finalized Paper account PnL
- position과 open order
- entry, protective OCO, reconcile, cutoff와 EOD flat 수명주기
- public surface는 read-only
- mutation은 기존 operator session과 Paper arm gate 밖에서 절대 열지 않음

### System

- M0-M10 status와 blocker
- launchd schedule, PID/exit/receipt, actual 실행 결과
- 단계별 stdout/stderr 요약과 실패 원인
- Railway deploy/health와 event relay 상태

## 데이터와 비용 경계

- dashboard snapshot schema v2를 추가한다.
- rolling deploy 중 server는 v1과 v2 ingest를 검증하고 publisher가 v2로 전환된 뒤 v1을
  제거한다.
- Mac mini에서 redaction을 마친 projection만 Railway로 보낸다.
- `watchfiles`와 단일 WebSocket event relay를 유지한다.
- 10초, 15초 또는 기타 주기적 HTTP/DB polling을 추가하지 않는다.
- 새 Railway worker를 만들지 않는다.
- 공개 조회는 access key 없이 유지한다.
- 명령은 Secure HttpOnly operator session에서만 허용한다.

## 구현 순서

1. `dashboard/DESIGN.md`를 승인된 방향과 전체 primitive/state 계약으로 갱신한다.
2. `docs/superpowers/specs/`에 dashboard v2 master design을 작성하고 커밋한다.
3. `docs/superpowers/plans/`에 실제 파일, TDD 단계와 검증 명령을 갖춘 vertical별 계획을
   작성한다.
4. primitive showcase를 먼저 구현하고 375/768/1280에서 통과시킨다.
5. snapshot schema v2와 Python read-only projector를 구현한다.
6. fixed sidebar app shell과 workspace routing을 구현한다.
7. persistent Hermes agent session과 Command Center를 구현한다.
8. Markets/Data, Research/Strategies, Derivatives/Paper, System 순서로 실제 vertical을
   연결한다.
9. 각 vertical을 작은 커밋으로 `origin/main`에 push하고 Railway에서 검증한다.

## 각 마일스톤 검증

- 구현 전후 `git status`
- TDD red-green-refactor
- dashboard typecheck, Biome와 Bun test/build
- 변경 Python 대상 pytest, Ruff와 basedpyright
- CLI `--help`, bad input, happy path
- loading, empty, error, blocked와 populated 상태
- 375, 768, 1280px visual QA와 키보드 QA
- live Railway URL에서 실제 snapshot/command/event delivery 확인
- credential, account identity, local path와 raw payload 비노출 확인

## 완료 기준

- 아홉 workspace가 모두 탐색 가능하다.
- 각 surface는 실제 데이터 또는 명시적 unavailable/blocked 상태를 표시한다.
- 임의 metric에서 Evidence Trace를 열어 source lineage를 확인할 수 있다.
- agent별 명령과 대화가 실제 persistent Hermes session으로 이어진다.
- Railway 공개 조회와 private operator 명령 경계가 유지된다.
- idle 상태에서 polling과 paid model 호출이 없다.
- 배포본이 desktop/tablet/mobile 수동 QA를 통과한다.
- 작업은 commit/push/Railway 검증까지 완료돼야 마일스톤 완료로 인정한다.

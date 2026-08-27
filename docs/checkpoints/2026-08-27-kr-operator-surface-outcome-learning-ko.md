# KR 자율 에이전트 운영 표면·결과 학습 체크포인트

- 검증일: 2026-08-27 (Asia/Seoul)
- 구현 기준 SHA: `de975770481b55850558c3f723ba2d8ec28749fd`
- 범위: 승인 설계 12.3, 한국시장 가상 추천·가상 포지션·결과 학습·운영자 표면

## 구현 결과

- 거래 결정과 가상 포지션 결과를 원본 수정 없이 `market` memory version으로 누적한다.
- 출처 군집, 테마, 시장 증거 상태, 장중 구간, 5/15/30분·장 마감 반응, 진입·손절·목표·censored 상태를 같은 lineage에 보존한다.
- 같은 실패가 세 개의 서로 다른 결과에서 반복되면 코드 변경 권한이 없는 Loop Engineer evidence bundle을 `self_improvement` memory로 만든다.
- 추천, 관망·기각, 가상 포지션, 결과 memory와 개선 bundle을 immutable source ID 기준으로 Hermes에 한 번만 투영한다.
- Dashboard Markets·Research·Paper가 같은 task → decision → virtual position → outcome → bundle trace를 query-only로 읽는다.
- Dashboard publisher의 최초 snapshot, reconnect, filesystem event watch가 schema-v4 operator 경로를 동일하게 전달한다.

## 자동 검증

- 핵심 수직·회귀: `48 passed`
- 광범위 KR·자율 Supervisor·Dashboard 회귀: `1298 passed, 1 failed`
- 남은 실패는 기준 SHA `ff1cf6bbeebb9a9fc02fecc9f0deaf1e0f863cec`에서도 존재하던 `test_child_import_closure_excludes_operational_authority`이다. 이번 변경 파일과 무관한 기존 import-closure 문제이며 수정하지 않았다.
- Ruff format/check: 통과
- basedpyright: 오류·경고 0
- Python no-excuse checker: 24개 변경 파일 위반 0
- `git diff --check`: 통과

## 수동 사용자 표면

- `run_research_agent_runtime.py --help`: 종료 코드 0
- 존재하지 않는 config로 `tick`: 종료 코드 2, 외부 호출·파일 변조 없음
- 임시 schema-v4 fixture tick: `status=blocked`, `projected_results=2`, `broker_mutation=0`
  - fixture Chrome 실행 파일이 실제 브라우저가 아니므로 연구 역할은 정직하게 blocked로 남았다.
  - 저장된 한국시장 결정과 결과는 Hermes 원장에 정상 투영됐다.
- current-main clone에서 Dashboard `--dry-run`: 종료 코드 0
  - Markets: `kr-decision-0f02579f802b86e89fe7d499`
  - Research: `kr-task-c0078040ac85b7256c21c9b0`, `kr-outcome-f55dc3dbcf34f10d2a7333bd`
  - Paper: `kr-position-c13f63915fd8e114e81c92d2`
  - 표시값은 `virtual`, entry, stop, targets, 검증 상태, 시장 상태, 장중 구간과 next wake를 포함했다.
  - API key, bearer, cookie, password, secret, token, 계좌 ID, raw payload/header/response, 사용자 절대경로 패턴 검출 수: 0

## 안전 경계와 해석

- KIS는 저장된 시장 receipt 조회만 사용했으며 주문·계좌·잔고·실포지션 API 호출은 0이다.
- LS 호출과 Alpaca 호출은 0이며, 실거래 경로를 추가하지 않았다.
- 모든 한국시장 체결·손익 상태는 가상 상태다. fixture, replay, horizon 반응은 수익성 증거가 아니다.
- Loop Engineer는 개선 증거 묶음만 만들며 코드를 자동 수정하거나 challenger를 승격하지 않는다.

## 남은 운영 관찰

- 자연 발생한 열린 KRX 세션에서 실제 브라우저 조사 → 추천 또는 관망 → 가상 포지션 → 장중 horizon version → Hermes/Dashboard 반영을 한 세션 관찰해야 한다.
- 이 항목은 외부 시장 시간과 실제 사이트 상태에 의존하는 운영 증거이며 구현 완료 조건과 분리한다.

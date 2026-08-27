# KR Loop Engineer 통제형 자가수정 구현 체크포인트

- 검증일: 2026-08-27 (Asia/Seoul)
- 구현 커밋: `e7b3899`
- 기준 설계: `docs/superpowers/specs/2026-08-26-local-autonomous-trading-agent-design.md`
- 구현 계획: `docs/superpowers/plans/2026-08-27-kr-loop-engineer-controlled-self-modification.md`
- 제품 경계: 한국시장 가상운영 및 Alpaca Paper 전용. 실거래 권한은 없다.

## 구현 결과

반복 실패 증거 묶음을 고정된 호스트 정책으로 분류하고, 허용된 구현 파일과 테스트 파일만 수정할 수 있는 Grok 작업 계약으로 변환한다. 후보는 hard-link를 공유하지 않는 로컬 독립 Git clone에서 생성되며, 검증이 끝난 binary patch만 mode `0600` 불변 산출물로 보존된다. 작업 clone은 실행 뒤 제거된다.

후보 수명주기는 content-addressed append-only SQLite 원장에 `detected → candidate_ready → shadowing → promoted/rejected → rolled_back` 순서로 기록된다. 검증 영수증, 미래 Shadow 영수증, 모의운영 건강 영수증과 release generation은 모두 원본 계보를 유지한다. 두 개의 서로 다른 미래 세션, 세션별 challenger 우위 `0.05` 이상, 오류·데이터 부적격·주문 불일치·연구 태스크 손실 0을 모두 만족해야 모의운영 release로 승격한다. 건강 임계 위반은 직전 모의 release로 한 번만 복귀시킨다.

연구 서비스는 장중 tick에서 긴 코딩 작업을 실행하지 않고 새로운 증거 묶음을 durable `detected` 후보로만 동기화한다. Local Agent Computer 스케줄러는 `run_kr_loop_engineer.py tick`으로 한 후보씩 처리할 수 있다. 상태, 승격, 기각, 롤백 계보는 Dashboard DAG와 Hermes 한국어 메시지에 투영되며 raw patch, 경로, 프롬프트, worker stdout, 계정·인증 정보는 내보내지 않는다.

## 고정 안전 경계

- 후보 코드에는 provider, broker, credential, risk kernel, endpoint policy, release policy 수정 권한이 없다.
- 후보와 모든 release는 `paper_only=true`, `trading_authority=false`, `policy_mutation_authority=false`다.
- 이 구현은 KIS·LS·Alpaca 네트워크 호출을 추가하지 않았다.
- 검증 과정에서 실제 broker/provider 요청과 실거래 호출은 0회였다.
- 실제 Grok 유료 호출은 검증에서 실행하지 않았다. 동일 계약과 독립 Git clone을 사용하는 bounded fixture worker로 변경·검증·patch·정리 수직 경로를 확인했다.

## 자동 검증 증거

다음 명령은 모두 종료 코드 0이었다.

1. 신규 수직 경로 집중 검증

   `uv run pytest -q tests/test_kr_loop_engineer_store.py tests/test_kr_loop_engineer_mutation.py tests/test_kr_loop_engineer_controller.py tests/test_kr_loop_engineer_cli.py tests/test_research_agent_service_kr_loop_engineer.py tests/test_kr_loop_engineer_operator_surface.py tests/test_kr_loop_engineer_vertical.py`

   결과: `15 passed in 1.95s`

2. KR 자율운영 회귀 검증

   신규 Loop Engineer, KR autonomous, autonomous KR tool/critic 테스트 묶음.

   결과: `95 passed in 20.21s`

3. 서비스·Dashboard/Hermes·Grok harness·forward evaluation 회귀 검증

   결과: `123 passed in 75.64s`

4. 거래 안전 경계 회귀 검증

   Alpaca Paper config/client/mutation, KIS 한국시장 read-only client/CLI, LS news/token 경계 테스트 묶음.

   결과: `80 passed in 2.06s`

5. 정적 검증

   - Ruff format/check: 통과
   - `uv run basedpyright` 전체 변경 Python 파일: `0 errors, 0 warnings, 0 notes`
   - Omo Python no-excuse checker 19개 production 파일: `no violations`
   - `git diff --check`: 통과

## 실제 CLI 수동 QA

- `uv run python run_kr_loop_engineer.py --help`: 종료 코드 0, `status/health/shadow/sync/tick` 노출 확인.
- mode `0600`의 잘못된 건강 영수증: 종료 코드 2, `invalid Loop Engineer request`만 출력, DB 미생성 확인.
- private 임시 원장에서 실제 CLI subprocess로 미래 Shadow 영수증 두 건과 건강 영수증 한 건 처리:
  - 첫 미래 세션: `shadowing`
  - 둘째 미래 세션: `promoted`
  - 오류율 `0.06` 건강 영수증: `rolled_back`
  - release action: `promote`, `rollback`
  - 출력: `paper_only=true`, `trading_authority=false`, 임시 경로 비노출

## 운영 관찰 유보 사항

2026-08-27 현재 실제로 서로 다른 두 한국시장 미래 세션이 경과하지 않았으므로 자연 장중 데이터에 의한 승격은 아직 관찰할 수 없다. 실제 후보는 향후 세션 영수증이 들어오기 전까지 `shadowing`에 머물며, 이 체크포인트는 결정론적 fixture 영수증으로 같은 승격·복귀 상태기계를 검증했다. 이는 안전상 의도된 대기 조건이며 수익성을 의미하지 않는다.

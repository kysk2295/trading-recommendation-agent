# Autonomous Supervisor Foundation 완료 게이트

기준 커밋은 `c978e373e8f012ad094789720ccc4d1264882bdf`이고, 게이트 수정 커밋은
`c993f3a5236d4445b1e20b35b7152667e3e12aa2`이다. 이번 릴리스는 수익성이나 실제
거래 성과를 주장하지 않으며, Foundation의 내구성·안전 경계만 검증한다.

## 완료 게이트 증적

| 시나리오 | 실행 | 이진 관측값 | 증적 |
| --- | --- | --- | --- |
| Foundation 및 이후 추가된 경계·보안·동시성·복구·due 회귀 | 계획의 14개 파일과 `test_autonomous_*` 경계/보안/복구, supervisor cutover/due, service boundary를 포함한 `uv run pytest -q ...` | `287 passed in 47.17s`, skip 0 | `.omo/evidence/task8/targeted-autonomous-regression-final.txt` |
| 변경 Python 전체 정적 게이트 | 기준 `0628960` 이후 57개와 이번 분리 테스트 2개에 Ruff, basedpyright, no-excuse 실행 | Ruff 통과, basedpyright `0 errors, 0 warnings, 0 notes`, no-excuse `no violations in 59 file(s)` | `.omo/evidence/task8/ruff-all-changed-python-final.txt`, `.omo/evidence/task8/basedpyright-all-changed-python-final-verified.txt`, `.omo/evidence/task8/no-excuse-all-changed-python-final.txt` |
| supervisor protocol 타입 경계 | `RecordingSupervisor.close()` 추가 및 runtime 테스트를 basic/safety 모듈로 분리 | Ruff 통과, `26 passed`, basedpyright `0/0/0`, no-excuse 통과 | `.omo/evidence/task8/runtime-test-split-ruff-final.txt`, `.omo/evidence/task8/runtime-test-split-pytest-final.txt`, `.omo/evidence/task8/runtime-test-split-basedpyright-final.txt`, `.omo/evidence/task8/runtime-test-split-no-excuse-final.txt` |
| CLI 도움말 | `uv run --offline python run_research_agent_runtime.py --help` | exit 0, `run`, `tick`, `cycle`, `status` 명령 노출 | `.omo/evidence/task8/cli-help.txt` |
| CLI 잘못된 입력 | `uv run --offline python run_research_agent_runtime.py status --config /tmp/trading-agent-missing-config.json --plist /tmp/trading-agent-missing.plist` | exit 2, traceback·secret 출력 없음 | `.omo/evidence/task8/cli-missing-input.txt` |
| 실제 읽기 전용 상태 | `research-agent-runtime-v11.json`과 기존의 검증된 `ai.trading-agent.research-agent-runtime-v11.plist`로 `status` 실행 | exit 0; `status=idle`, family 6, `model_calls=0`, `broker_mutation=0`, 다음 wake 존재, 민감 필드명 없음 | `.omo/evidence/task8/cli-v11-matching-pair-semantic-status.txt` |
| launchd 독립성 | 설치 plist를 읽기 전용으로 Label/KeepAlive/RunAtLoad/ProgramArguments 검사 | label 정확, KeepAlive=true, RunAtLoad=true, repo의 `run_research_agent_runtime.py run` 명령, Codex/session/browser/chat/token 의존성 없음 | `.omo/evidence/task8/launchd-readonly-inspection.txt` |
| Alpaca Paper 사전 네트워크 차단 | `uv run pytest -q tests/test_alpaca_paper_client.py tests/test_alpaca_paper_mutation_client.py tests/test_paper_operating_mutation_execution.py` | `33 passed`; live/non-paper URL용 MockTransport opener는 호출 시 즉시 실패하도록 두었고 생성자 경계에서 `NonPaperTradingEndpointError` 발생 | `.omo/evidence/task8/alpaca-paper-pre-network-rejection.txt`, `.omo/evidence/task8/alpaca-paper-pre-network-test-collection.txt` |

## 실제 환경 상태의 경계

계획에 적힌 정확한 v11 config와 **설치된 무버전 plist** 조합은 exit 2이다. config 자체는
canonical로 읽히지만 plist의 ProgramArguments가 다른 config를 가리키므로 launchd pair 검증이
실패한다. 외부 config/plist는 수정하지 않았다. 전체 read-only inventory에서는 기존 v11 쌍이
검증되며, 그 쌍을 보조 읽기 전용 상태 검사에만 사용했다.

v11 output root의 autonomous task database는 존재하지 않아 생성·수정하지 않고 상태 query를
차단했다. 기존 persisted Research OS status도 `autonomous_supervisor` 필드가 없는 과거 외부
배포 산출물이다. 따라서 위 CLI 관측은 broker/model mutation 0의 상태 표면 검증일 뿐, 이
worktree Foundation의 실제 상시 배포나 durable supervisor task 수를 주장하지 않는다.

관련 증적: `.omo/evidence/task8/cli-real-readonly-status-raw.txt`,
`.omo/evidence/task8/cli-status-pair-diagnosis.txt`,
`.omo/evidence/task8/available-config-plist-readonly-inventory.txt`,
`.omo/evidence/task8/config-backup-pair-readonly-verification.txt`,
`.omo/evidence/task8/v11-autonomous-supervisor-readonly-status.txt`,
`.omo/evidence/task8/v11-persisted-os-status-semantic-inspection.txt`.

## 완료 범위와 다음 단계

코드/테스트 게이트는 production builder의 모든 family supervisor 설치, 다단계 tool observation,
restart replay, no-trade future wake, 반복 실패 lineage, Research OS status model, launchd의 Codex
독립성, Alpaca Paper의 HTTP 이전 URL 차단을 검증한다. 외부 launchd/config 배포 상태는 이
worktree의 수정 범위가 아니다.

다음에는 **KR natural-market vertical** 계획을 먼저 작성·실행한다. 그 다음 순서는 **US Alpaca
Paper vertical**, 마지막은 **Loop Engineer**이며, 이 Foundation만으로 Paper mutation 또는
수익성 주장을 하지 않는다.

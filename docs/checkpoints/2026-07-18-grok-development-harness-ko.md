# Grok Development Harness 체크포인트

- 날짜: 2026-07-18
- 범위: 개발 작업 격리·검증 도구 (in-place bounded worker)
- provider network access: 0건
- credential loading: 0건
- broker mutation: 0건

## 구현

- `GrokTaskContract`는 base commit, 허용 파일, 검증·수동 QA 명령과 worker summary 필드를 immutable JSON contract로 고정한다.
- absolute/traversal path, `.git`·`.grok`·`.hermes`, 환경·비밀 관련 경로, 중복/빈 경로, shell metacharacter가 있는 명령, 잘못된 commit과 unknown field는 sanitized error로 거절한다.
- runner는 exact HEAD와 contract base를 대조하고, checkout이 clean이거나 사전 존재하는 user-owned `?? .hermes/` 및/또는 `?? .omo/` 상태일 때만 진행한다.
- dry-run은 Grok process를 호출하지 않고 Git 상태도 바꾸지 않는다.
- 실제 worker는 worktree/branch/clone 없이 현재 repository root에서 in-place non-interactive로 실행한다. allow-list 안 working-tree 파일은 수정할 수 있지만 main history에 commit/push 하면 안 된다.
- 생성 명령은 `--cwd`(repository root), `--always-approve`, `--permission-mode bypassPermissions`, `-p` single-turn, `--output-format json`, strict `--json-schema`(changed_files/verification/concerns), `--no-plan`, `--no-subagents`, `--disable-web-search`, `--no-memory`, `--max-turns`를 사용한다. `--sandbox strict`와 branch/worktree 생성은 사용하지 않는다.
- 실행 후 `git rev-parse HEAD`가 contract base와 같아야 한다. worker commit이 있으면 fail-closed다. Git changed path 전체를 allow-list와 대조하고 허용 밖 경로는 fail-closed다.
- timeout/OSError 경로에서도 HEAD 불변 확인 뒤 allow-list를 강제한다. 허용 밖 변경을 failure report로 숨기지 않는다.
- public report는 top-level `structuredOutput`에서만 bounded summary를 파싱한다. `text`의 연결 draft JSON에 의존하지 않으며 raw stdout/stderr, prompt, objective, absolute path, credential, provider payload는 노출하지 않는다.
- summary `changed_files` 순서는 Git `changed_paths`와 달라도 되지만, 중복 없이 동일한 unique path 집합이어야 한다. 누락·추가·중복은 `worker_failed`이며 untrusted summary는 노출하지 않는다.
- `expected_summary_fields`는 정확히 `changed_files`, `verification`, `concerns`여야 한다.
- CLI는 `--worktree-root`를 요구하지 않는다. nonzero/timeout/missing binary는 안전한 `worker_failed`로 보존한다.

## 현재 상태

현재 real in-place Grok worker 실행과 후속 review correction이 성공했다. harness는 `structuredOutput`만 소비하고, Codex review 지점(HEAD 고정, path allow-list, summary path trust, timeout allow-list)을 반영한 상태다. 초기 bootstrap 시점의 account spending limit HTTP `402`는 과거 맥락으로만 남긴다. 현재 제한이 아니다.

## 검증

- task contract validation: targeted pytest
- Git preflight, dry-run, worker nonzero, structured summary, and out-of-scope changed-path rejection: temporary Git repository pytest
- CLI `--help`, malformed contract, and dry-run happy path: subprocess pytest
- focused harness pytest, Ruff, basedpyright, compileall, and CLI help/invalid/dry-run QA

## 운영 규칙

`run_grok_task.py`는 거래 런타임이 아니다. 작업마다 새 contract를 base commit에 맞춰 만들고, worker가 끝난 뒤 Codex가 contract compliance와 code quality를 독립 review한다. 승인 전에는 `main` commit 또는 push를 수행하지 않는다.

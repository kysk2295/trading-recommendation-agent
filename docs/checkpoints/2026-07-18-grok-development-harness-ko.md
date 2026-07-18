# Grok Development Harness 체크포인트

- 날짜: 2026-07-18
- 범위: 개발 작업 격리·검증 도구 (in-place bounded worker)
- provider network access: 0건
- credential loading: 0건
- broker mutation: 0건

## 구현

- `GrokTaskContract`는 base commit, 허용 파일, 검증·수동 QA 명령과 worker summary 필드를 immutable JSON contract로 고정한다.
- absolute/traversal path, `.git`·`.grok`·`.hermes`·`.omo`, 환경·비밀 관련 경로, 중복/빈 경로, path/command 개수·길이 상한 초과, shell metacharacter가 있는 명령, 잘못된 commit과 unknown field는 sanitized error로 거절한다.
- runner는 exact HEAD와 contract base를 대조하고, checkout이 clean이거나 사전 존재하는 user-owned `?? .hermes/` 및/또는 `?? .omo/` 상태일 때만 진행한다.
- preflight는 `main` branch 전용이며 linked worktree와 symlink repository root를 거절한다. 실행 전·후 workspace snapshot은 user-owned/ignored 파일과 Git control path(`.git/HEAD`, `.git/config`, optional `.git/config.worktree`, `.git/packed-refs`, `.git/info/exclude`, `.git/hooks/*`)를 **내용을 읽지 않고** immutable metadata tuple(mode/uid/size/mtime_ns/ctime_ns)로 비교하며 refs/reflog/object inventory도 포함한다. `.git/index`는 status refresh가 쓸 수 있어 snapshot하지 않는다. commit 후 reset으로 HEAD만 되돌리거나 local config/hook을 바꾸는 것도 fail-closed다. allowed path의 symlink component도 실행 전·후에 거절한다.
- dry-run은 Grok process를 호출하지 않고 Git 상태도 바꾸지 않는다.
- 실제 worker는 worktree/branch/clone 없이 현재 repository root에서 in-place non-interactive로 실행한다. allow-list 안 working-tree 파일은 수정할 수 있지만 main history에 commit/push 하면 안 된다.
- 생성 명령은 `--cwd`(repository root), `--always-approve`, `--permission-mode bypassPermissions`, `-p` single-turn, `--output-format json`, strict `--json-schema`(changed_files/verification/concerns), `--no-plan`, `--no-subagents`, `--disable-web-search`, `--no-memory`, `--max-turns`를 사용한다. `--sandbox strict`와 branch/worktree 생성은 사용하지 않는다.
- worker process는 새 process group에서 실행되고 stdout은 임시 regular file로 리다이렉트한 뒤 size/deadline를 poll한다. stderr는 DEVNULL이며 timeout·oversize 시 process group을 kill하고 parent 종료 후 survivor도 정리한다. stderr는 반환하지 않는다.
- 실행 후 `git rev-parse HEAD`와 Git database/control fingerprint가 contract base snapshot과 같아야 한다. worker commit·ref/object·local config/hook 변경이 있으면 fail-closed다. Git changed path 전체를 allow-list와 대조하고 허용 밖 경로는 fail-closed다.
- timeout/OSError 경로에서도 snapshot·allow-list를 강제한다. 허용 밖 변경을 failure report로 숨기지 않는다.
- public report는 top-level `structuredOutput`에서만 bounded summary를 파싱한다. `text`의 연결 draft JSON에 의존하지 않으며 raw stdout/stderr, prompt, objective, absolute path, credential, provider payload는 노출하지 않는다.
- summary `changed_files`는 contract allow-list subset이어야 하고, Git `changed_paths`와 중복 없이 동일한 unique path 집합이어야 한다. `verification`은 required+manual 명령의 exact unique set과 일치해야 하며 subset/empty/duplicate는 거절한다. `concerns`는 고정 enum만 허용한다.
- completed 전에 harness가 required/manual QA 명령을 `uv run --offline`으로 독립 재실행하며 stdout/stderr는 DEVNULL이다. timeout/OSError는 worker_failed다.
- `expected_summary_fields`는 정확히 `changed_files`, `verification`, `concerns`여야 한다.
- CLI는 `--worktree-root`를 요구하지 않는다. nonzero/timeout/missing binary는 안전한 `worker_failed`로 보존한다.

## Residual risk (prompt-only)

`--sandbox strict`를 쓰지 않으므로 credential 읽기, network 호출, `git push`, allow-list 밖 external write 방지는 OS sandbox가 아니라 prompt/contract/post-condition 검사에만 의존한다. 이 residual risk는 의도된 한계이며, Codex independent review가 최종 통합 전 통제다.

## 현재 상태

현재 real in-place Grok worker 실행과 후속 review correction·hardening이 반영됐다. harness는 `structuredOutput`만 소비하고, main-only preflight, process-group timeout kill, Git database fingerprint, user-owned/ignored metadata 보호, offline verification re-run, summary contract-safe enum을 강제한다.

## 검증

- task contract validation: targeted pytest
- Git preflight, dry-run, worker nonzero, structured summary, out-of-scope changed-path rejection: temporary Git repository pytest
- CLI `--help`, malformed contract, and dry-run happy path: subprocess pytest
- focused harness pytest, Ruff, basedpyright, compileall, and CLI help/invalid/dry-run QA

## 운영 규칙

`run_grok_task.py`는 거래 런타임이 아니다. 작업마다 새 contract를 base commit에 맞춰 만들고, worker가 끝난 뒤 Codex가 contract compliance와 code quality를 독립 review한다. 승인 전에는 `main` commit 또는 push를 수행하지 않는다.

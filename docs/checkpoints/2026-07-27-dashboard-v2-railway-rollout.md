# Dashboard v2 Railway rollout

Date: 2026-07-27 (KST)

## Production binding

- Project: `ee149dc8-82b8-46e7-8ef7-582400fed6f9`
- Environment: `8b37a20f-6b0d-4137-a787-ad90b4b482b9`
- Application service: `observatory` (`a7cae053-9289-4120-b5ac-7a0aefc36778`)
- Preserved database service: `Postgres` (`21b11148-2386-47a4-b2dd-2a8dfbce94bd`)
- Public URL: <https://observatory-production-3172.up.railway.app>
- Service count after rollout: exactly 2

No Railway worker service was created. Provider and live-money state were not
mutated.

## Rollout result

The first compatibility deployment from `846f60ad86918baa1961bb94a5cbe7e22f960ace`
exposed legacy persisted interaction rows that no longer matched the strict current
interaction enum. Deployment `7a414b90-4606-4981-a4ce-370eb0920b3d` was therefore
rejected after it crashed.

The bounded compatibility fix
`7eec367bfff69854c4e084e3032907140299677f` skips only persisted rows that fail the
current strict interaction schema. Focused tests, TypeScript, Biome, and the
dashboard production build passed before deployment. Recovery deployment
`9fa01407-18a0-4e9a-9173-e4ee58af3a4c` reached `SUCCESS`, returned HTTP 200 from
`/api/health`, and served a v2 snapshot sourced from v2.

## Live acceptance

- Public health and snapshot reads returned HTTP 200. The snapshot reported
  `schema_version: 2` and `projection.source_schema_version: 2`.
- A public command attempt returned HTTP 401. Pairing produced a
  `Secure; HttpOnly; SameSite=Strict` operator cookie and an authenticated operator
  session.
- The public viewer received v2 WebSocket events. Operator receipts remained
  private and passed the recursive forbidden-data scan.
- One explicit read-only conversation produced one receipt, one process start, no
  retry, and one completed result. Restarting the publisher retained the same
  agent-family binding without disclosing the private session identifier.
- One directed source-evidence job emitted ordered progress, evidence, and result
  receipts. The evidence and result digests matched the local artifacts. It
  produced one claim/process and no retry.
- One authorized sealed fake/safe autonomous research trigger produced one claim
  and one model process, then emitted claim, progress, evidence, result, and cleanup
  receipts. Two duplicate submissions produced zero additional claims/processes.
  A separate missing-authority trigger emitted a blocker and produced zero
  claims/processes.
- A corrected five-minute true-idle trace used one viewer WebSocket and observed
  zero idle API requests, zero Hermes launches, zero autonomous launches, and one
  stable operational publisher. An earlier process-wide sampler was discarded
  because it included unrelated host activity.
- Recursive scans of live payloads, browser state, receipts, and recorded artifacts
  found no secret, cookie value, session identifier, account identity, local path,
  or worktree identifier.

## Compatibility rollback and recovery

The original v1 commit
`042ff7ce3df760eef42bff611fefbe5170bb2220` was deployed as
`65875744-f5e7-43f0-a3da-9e87cf14f3d3`. Its server could not remain healthy because
its pre-v2 interaction reader strictly parses newer persisted interaction rows, so
the deployment was immediately removed and production recovered to the pushed v2
compatibility SHA.

To isolate the required retained-data compatibility from that known interaction
reader limitation, the unmodified original v1 `dashboardSnapshotSchema` client read
the production `dashboard_snapshots` singleton directly from the preserved
Postgres service. It found one row and strictly parsed it as `schema_version: 1`;
the forbidden-data scan passed. Production recovery then returned HTTP 200 health
and a current v2/source-v2 snapshot.

## Evidence

The Todo 13 evidence bundle is stored at
`.omo/evidence/dashboard-v2/task-13/`. Its acceptance matrix names the exact
scenario, invocation, binary observable, and artifact for every claim.

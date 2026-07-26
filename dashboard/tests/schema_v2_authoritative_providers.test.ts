import { afterAll, describe, expect, test } from "bun:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { resolveEvidenceTrace } from "../src/evidence_trace";
import { dashboardSnapshotV2Schema } from "../src/schema";
import { providerEvidencePresentation } from "../src/workspaces/data_sources";

const repository = join(import.meta.dir, "..", "..");
const output = mkdtempSync(join(tmpdir(), "dashboard-provider-v2-"));
const canaries = [
  "alfred_private_token",
  "opendart_private_token",
  "kis_private_token",
  "ls_private_token",
  "treasury_private_token",
];

afterAll(() => {
  rmSync(output, { recursive: true, force: true });
});

describe("authoritative provider projector boundary", () => {
  test("parses five native positive providers without recursive receipt leakage", () => {
    const generated = Bun.spawnSync(
      ["uv", "run", "python", "-m", "tests.dashboard_provider_positive_fixture", output],
      {
        cwd: repository,
        stderr: "pipe",
        stdout: "pipe",
      },
    );

    expect(generated.exitCode).toBe(0);
    expect(generated.stderr.toString()).toBe("");
    const parsed = dashboardSnapshotV2Schema.parse(JSON.parse(generated.stdout.toString()));
    const capabilities = new Map(
      parsed.workspaces.data_sources.capabilities.map((capability) => [
        capability.provider,
        capability,
      ]),
    );

    for (const provider of ["alfred", "treasury", "opendart", "kis", "ls"] as const) {
      expect(capabilities.get(provider)).toMatchObject({
        entitlement: "research_only",
        state: "populated",
      });
    }
    const alfred = capabilities.get("alfred");
    if (alfred === undefined) throw new Error("missing authoritative ALFRED capability");
    const trace = resolveEvidenceTrace(alfred.trace_id, parsed.traces.nodes, parsed.traces.edges);
    const display = providerEvidencePresentation(
      "alfred",
      alfred,
      trace,
      parsed.workspaces.data_sources.capabilities.map((capability) => capability.trace_id),
    );

    expect(trace.nodes).toHaveLength(1);
    expect(trace.edges).toHaveLength(0);
    expect(trace.terminal).toMatchObject({
      node_id: alfred.trace_id,
      kind: "source_receipt",
      state: "accepted",
      source_namespace: "provider.alfred",
    });
    expect(display).toMatchObject({
      state: "populated",
      receipt: "Receipt · Provider-specific typed authority",
    });
    expect(containsLeak(parsed)).toBe(false);
  });
});

function containsLeak(value: unknown): boolean {
  if (typeof value === "string") {
    const lowered = value.toLowerCase();
    return canaries.some((canary) => lowered.includes(canary));
  }
  if (Array.isArray(value)) {
    return value.some(containsLeak);
  }
  if (value !== null && typeof value === "object") {
    return Object.entries(value).some(([key, item]) => forbiddenKey(key) || containsLeak(item));
  }
  return false;
}

function forbiddenKey(key: string): boolean {
  const lowered = key.toLowerCase();
  return ["api_key", "secret", "token", "credential", "authorization"].some((part) =>
    lowered.includes(part),
  );
}

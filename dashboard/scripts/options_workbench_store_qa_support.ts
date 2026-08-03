import { readFile } from "node:fs/promises";
import { type DashboardSnapshotV2, dashboardSnapshotV2Schema } from "../src/schema_v2";

const requiredBlockedLabels = ["missing", "corrupt", "stale", "unlicensed_current"] as const;

export type BlockedSnapshotLabel = (typeof requiredBlockedLabels)[number];

export type StoreQaOptions =
  | Readonly<{ kind: "help" }>
  | Readonly<{
      kind: "run";
      actualPath: string;
      blocked: readonly Readonly<{ label: BlockedSnapshotLabel; path: string }>[];
      output: string;
      widths: readonly number[];
    }>;

export type LoadedSnapshot = Readonly<{
  label: string;
  snapshot: DashboardSnapshotV2;
}>;

export const STORE_QA_HELP_TEXT = `Usage: bun run qa:options-workbench-store -- --actual <snapshot.json> --blocked <label=snapshot.json> --blocked <label=snapshot.json> --blocked <label=snapshot.json> --blocked <label=snapshot.json> --output <report.json> [--widths 375,768,1280]

Required blocked labels: missing, corrupt, stale, unlicensed_current.

Options:
  --help                 Show this help and exit.
  --actual <path>        Required actual DashboardSnapshotV2 JSON file.
  --blocked <label=path> Required once for every blocked label.
  --output <path>        Required JSON report destination.
  --widths <csv>         One to six unique integer widths from 320 to 2560.`;

export function parseStoreQaOptions(args: readonly string[]): StoreQaOptions {
  if (args.includes("--help")) return { kind: "help" };
  let actualPath: string | null = null;
  let output: string | null = null;
  let widths = "375,768,1280";
  const blocked: Readonly<{ label: BlockedSnapshotLabel; path: string }>[] = [];
  for (let index = 0; index < args.length; index += 1) {
    const option = args[index];
    if (option === undefined) continue;
    const value = args[index + 1];
    switch (option) {
      case "--actual":
        actualPath = requiredValue(value, option);
        index += 1;
        break;
      case "--blocked":
        blocked.push(parseBlocked(requiredValue(value, option)));
        index += 1;
        break;
      case "--output":
        output = requiredValue(value, option);
        index += 1;
        break;
      case "--widths":
        widths = requiredValue(value, option);
        index += 1;
        break;
      default:
        throw new OptionsWorkbenchStoreQaError(`unknown option: ${option}`);
    }
  }
  if (actualPath === null) throw new OptionsWorkbenchStoreQaError("--actual is required");
  if (output === null) throw new OptionsWorkbenchStoreQaError("--output is required");
  assertBlockedLabels(blocked);
  return { kind: "run", actualPath, blocked, output, widths: parseWidths(widths) };
}

export async function loadSnapshot(path: string, label: string): Promise<LoadedSnapshot> {
  let decoded: unknown;
  try {
    decoded = JSON.parse(await readFile(path, "utf8"));
  } catch (error: unknown) {
    if (error instanceof Error) {
      throw new OptionsWorkbenchStoreQaError(
        `${label} snapshot could not be read: ${error.message}`,
      );
    }
    throw error;
  }
  return { label, snapshot: dashboardSnapshotV2Schema.parse(decoded) };
}

export function reachableBlockerTerminal(
  snapshot: DashboardSnapshotV2,
  traceId: string,
): Readonly<{ nodeId: string; label: string }> {
  const nodes = new Map(snapshot.traces.nodes.map((node) => [node.node_id, node]));
  const queue = [traceId];
  const visited = new Set(queue);
  for (const nodeId of queue) {
    const node = nodes.get(nodeId);
    if (node?.kind === "blocker_terminal") return { nodeId: node.node_id, label: node.label };
    for (const edge of snapshot.traces.edges) {
      if (edge.from_node_id === nodeId && !visited.has(edge.to_node_id)) {
        visited.add(edge.to_node_id);
        queue.push(edge.to_node_id);
      }
    }
  }
  throw new OptionsWorkbenchStoreQaError(`no reachable blocker terminal from ${traceId}`);
}

function requiredValue(value: string | undefined, option: string): string {
  if (value === undefined || value.length === 0 || value.startsWith("--")) {
    throw new OptionsWorkbenchStoreQaError(`${option} requires a value`);
  }
  return value;
}

function parseBlocked(value: string): Readonly<{ label: BlockedSnapshotLabel; path: string }> {
  const separator = value.indexOf("=");
  const label = value.slice(0, separator);
  const path = value.slice(separator + 1);
  if (separator < 1 || path.length === 0 || !isBlockedSnapshotLabel(label)) {
    throw new OptionsWorkbenchStoreQaError(
      "--blocked must use missing|corrupt|stale|unlicensed_current=<path>",
    );
  }
  return { label, path };
}

function isBlockedSnapshotLabel(value: string): value is BlockedSnapshotLabel {
  return requiredBlockedLabels.some((candidate) => candidate === value);
}

function assertBlockedLabels(
  blocked: readonly Readonly<{ label: BlockedSnapshotLabel; path: string }>[],
): void {
  const labels = blocked.map((entry) => entry.label);
  for (const label of requiredBlockedLabels) {
    if (labels.filter((candidate) => candidate === label).length !== 1) {
      throw new OptionsWorkbenchStoreQaError(`--blocked ${label}=<path> is required exactly once`);
    }
  }
}

function parseWidths(raw: string): readonly number[] {
  const widths = raw.split(",").map((value) => Number(value));
  if (
    widths.length < 1 ||
    widths.length > 6 ||
    widths.some((width) => !Number.isInteger(width) || width < 320 || width > 2560) ||
    new Set(widths).size !== widths.length
  ) {
    throw new OptionsWorkbenchStoreQaError(
      "--widths must contain one to six unique integers from 320 to 2560",
    );
  }
  return widths;
}

export class OptionsWorkbenchStoreQaError extends Error {
  override readonly name = "OptionsWorkbenchStoreQaError";
}

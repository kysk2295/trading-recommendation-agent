import { z } from "zod";

export type IdleObservation = {
  readonly storeEvents: number;
  readonly observedProcessIds: readonly number[];
};

export type IdleDelta = {
  readonly storeOperations: number;
  readonly processLaunches: number;
};

export type ProcessObservation = {
  readonly publisherProcessIds: readonly number[];
  readonly hermesProcessIds: readonly number[];
  readonly autonomousProcessIds: readonly number[];
};

const storeEventSchema = z.strictObject({
  type: z.literal("store_operation"),
  operation: z.string().min(1),
  observed_at: z.iso.datetime({ offset: true }),
  pid: z.number().int().positive(),
});

export function observedIdleDelta(
  baseline: IdleObservation,
  end: IdleObservation,
): IdleDelta {
  const baselineProcesses = new Set(baseline.observedProcessIds);
  return {
    storeOperations: end.storeEvents - baseline.storeEvents,
    processLaunches: end.observedProcessIds.filter((pid) => !baselineProcesses.has(pid)).length,
  };
}

export async function storeEventCount(path: string): Promise<number> {
  const file = Bun.file(path);
  if (!(await file.exists())) {
    throw new IdleObservationError("store observation log is unavailable");
  }
  const text = await file.text();
  if (text.length === 0) return 0;
  const lines = text.split("\n").filter((line) => line.length > 0);
  for (const line of lines) {
    storeEventSchema.parse(JSON.parse(line));
  }
  return lines.length;
}

type ProcessRecord = {
  readonly pid: number;
  readonly parentPid: number;
  readonly command: string;
};

export async function observeProcesses(rootProcessIds: readonly number[] = []): Promise<ProcessObservation> {
  const process = Bun.spawn(["ps", "-axo", "pid=,ppid=,command="], {
    stdout: "pipe",
    stderr: "pipe",
  });
  const [stdout, stderr, exitCode] = await Promise.all([
    new Response(process.stdout).text(),
    new Response(process.stderr).text(),
    process.exited,
  ]);
  if (exitCode !== 0) {
    throw new IdleObservationError(`process observation failed: ${stderr.trim()}`);
  }
  const publisherProcessIds: number[] = [];
  const hermesProcessIds: number[] = [];
  const autonomousProcessIds: number[] = [];
  const records: ProcessRecord[] = [];
  for (const line of stdout.split("\n")) {
    const match = /^\s*(\d+)\s+(\d+)\s+(.+)$/.exec(line);
    if (match === null) continue;
    const pid = Number.parseInt(match[1] ?? "", 10);
    const parentPid = Number.parseInt(match[2] ?? "", 10);
    const command = match[3] ?? "";
    records.push({ pid, parentPid, command });
  }
  const processMap = new Map(records.map((record) => [record.pid, record]));
  for (const { pid, command } of records) {
    if (rootProcessIds.length > 0 && !isDescendant(pid, rootProcessIds, processMap)) continue;
    if (command.includes("run_dashboard_publisher.py") && command.includes("publish")) {
      publisherProcessIds.push(pid);
    }
    if (/(^|[/ ])hermes( |$)|hermes_cli/.test(command)) hermesProcessIds.push(pid);
    if (
      (command.includes("run_dashboard_publisher.py") && command.includes("autonomous-agent")) ||
      command.includes("dashboard_autonomous")
    ) {
      autonomousProcessIds.push(pid);
    }
  }
  return { publisherProcessIds, hermesProcessIds, autonomousProcessIds };
}

function isDescendant(
  pid: number,
  roots: readonly number[],
  processes: ReadonlyMap<number, ProcessRecord>,
): boolean {
  const rootSet = new Set(roots);
  let current = pid;
  const visited = new Set<number>();
  while (!visited.has(current)) {
    if (rootSet.has(current)) return true;
    visited.add(current);
    const record = processes.get(current);
    if (record === undefined || record.parentPid === current) return false;
    current = record.parentPid;
  }
  return false;
}

export function mergeObservedProcesses(
  target: {
    readonly publisher: Set<number>;
    readonly hermes: Set<number>;
    readonly autonomous: Set<number>;
  },
  observation: ProcessObservation,
): void {
  for (const pid of observation.publisherProcessIds) target.publisher.add(pid);
  for (const pid of observation.hermesProcessIds) target.hermes.add(pid);
  for (const pid of observation.autonomousProcessIds) target.autonomous.add(pid);
}

export class IdleObservationError extends Error {
  override readonly name = "IdleObservationError";
}

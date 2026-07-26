import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { parseArgs } from "node:util";
import ky from "ky";
import { chromium } from "playwright";
import {
  mergeObservedProcesses,
  observedIdleDelta,
  observeProcesses,
  storeEventCount,
} from "./idle_qa_support";

type IdleQaReport = {
  readonly observable: "DASHBOARD_TRUE_IDLE_OK";
  readonly durationSeconds: number;
  readonly sampleIntervalMs: number;
  readonly websocketConnections: number;
  readonly initialBrowserApiRequests: readonly string[];
  readonly idleBrowserApiRequests: readonly string[];
  readonly baselineStoreEvents: number;
  readonly endStoreEvents: number;
  readonly idleStoreOperations: number;
  readonly baselinePublisherProcessIds: readonly number[];
  readonly endPublisherProcessIds: readonly number[];
  readonly observedHermesProcessIds: readonly number[];
  readonly observedAutonomousProcessIds: readonly number[];
  readonly hermesProcessLaunches: number;
  readonly autonomousProcessLaunches: number;
  readonly instrumentation: {
    readonly server: "bun src/server.ts";
    readonly publisher: "uv run run_dashboard_publisher.py publish";
    readonly store: "ObservedSnapshotStore JSONL";
    readonly processes: "ps -axo pid=,command=";
  };
};

class IdleQaError extends Error {
  override readonly name = "IdleQaError";
}

const SAMPLE_INTERVAL_MS = 100;
const { values } = parseArgs({
  options: {
    "base-url": { type: "string", default: "http://127.0.0.1:3000" },
    "duration-seconds": { type: "string", default: "300" },
    output: { type: "string" },
    repository: { type: "string", default: ".." },
  },
  strict: true,
});
const output = required(values.output, "--output");
const durationSeconds = Number.parseInt(values["duration-seconds"], 10);
if (!Number.isInteger(durationSeconds) || durationSeconds < 1 || durationSeconds > 600) {
  throw new IdleQaError("--duration-seconds must be an integer from 1 to 600");
}
const baseUrl = new URL(values["base-url"]);
const port = baseUrl.port;
if (baseUrl.protocol !== "http:" || baseUrl.hostname !== "127.0.0.1" || port.length === 0) {
  throw new IdleQaError("--base-url must be an explicit 127.0.0.1 HTTP port");
}
const repository = resolve(values.repository);
const observationLog = resolve(dirname(output), "idle-store-observations.jsonl");
await mkdir(dirname(resolve(output)), { recursive: true });
await writeFile(observationLog, "", { mode: 0o600 });
const runtime = await mkdtemp(join(dirname(resolve(output)), "idle-runtime-"));
const outputs = join(runtime, "outputs");
const credentials = join(runtime, "dashboard.env");
await mkdir(outputs, { mode: 0o700 });
const ingestToken = "idle-fixture-ingest-token-1234567890";
const operatorToken = "idle-fixture-operator-token-1234567890";
await writeFile(
  credentials,
  `DASHBOARD_URL=${baseUrl.toString().replace(/\/$/, "")}\nDASHBOARD_INGEST_TOKEN=${ingestToken}\n`,
  { mode: 0o600 },
);
const server = Bun.spawn(["bun", "src/server.ts"], {
  cwd: resolve(repository, "dashboard"),
  env: {
    ...process.env,
    PORT: port,
    DASHBOARD_INGEST_TOKEN: ingestToken,
    DASHBOARD_OPERATOR_TOKEN: operatorToken,
    DASHBOARD_OBSERVATION_LOG: observationLog,
  },
  stdout: "pipe",
  stderr: "pipe",
});
let publisher: ReturnType<typeof Bun.spawn> | undefined;
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

try {
  await waitForServer(baseUrl.toString());
  publisher = Bun.spawn(
    [
      "uv",
      "run",
      "run_dashboard_publisher.py",
      "publish",
      "--outputs",
      outputs,
      "--credentials",
      credentials,
      "--system-authority-config",
      join(runtime, "missing-system-authority.json"),
    ],
    { cwd: repository, stdout: "pipe", stderr: "pipe" },
  );
  const initialBrowserApiRequests: string[] = [];
  const idleBrowserApiRequests: string[] = [];
  let measurementStarted = false;
  let websocketConnections = 0;
  page.on("websocket", () => {
    websocketConnections += 1;
  });
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.origin !== baseUrl.origin || !url.pathname.startsWith("/api/")) return;
    (measurementStarted ? idleBrowserApiRequests : initialBrowserApiRequests).push(url.pathname);
  });
  await page.goto(`${baseUrl.toString()}#command-center`, { waitUntil: "networkidle" });
  const rootProcessIds = [server.pid, publisher.pid];
  await waitForInstrumentation(observationLog, publisher.pid, rootProcessIds);
  const baselineStoreEvents = await storeEventCount(observationLog);
  const baselineProcesses = await observeProcesses(rootProcessIds);
  if (!baselineProcesses.publisherProcessIds.includes(publisher.pid)) {
    throw new IdleQaError("publisher PID was not observable at measurement baseline");
  }
  const observed = {
    publisher: new Set(baselineProcesses.publisherProcessIds),
    hermes: new Set(baselineProcesses.hermesProcessIds),
    autonomous: new Set(baselineProcesses.autonomousProcessIds),
  };
  measurementStarted = true;
  await sampleForDuration(durationSeconds, observed, rootProcessIds);
  const endProcesses = await observeProcesses(rootProcessIds);
  mergeObservedProcesses(observed, endProcesses);
  const endStoreEvents = await storeEventCount(observationLog);
  const storeDelta = observedIdleDelta(
    { storeEvents: baselineStoreEvents, observedProcessIds: [] },
    { storeEvents: endStoreEvents, observedProcessIds: [] },
  );
  const newHermes = withoutBaseline(observed.hermes, baselineProcesses.hermesProcessIds);
  const newAutonomous = withoutBaseline(
    observed.autonomous,
    baselineProcesses.autonomousProcessIds,
  );
  requireZero(idleBrowserApiRequests.length, "idle browser API requests");
  requireZero(storeDelta.storeOperations, "idle store operations");
  requireZero(newHermes.length, "Hermes/model process launches");
  requireZero(newAutonomous.length, "autonomous process launches");
  if (!endProcesses.publisherProcessIds.includes(publisher.pid)) {
    throw new IdleQaError("publisher PID was not observable at measurement end");
  }
  if (websocketConnections !== 1) {
    throw new IdleQaError(`expected one viewer WebSocket, received ${websocketConnections}`);
  }
  const report: IdleQaReport = {
    observable: "DASHBOARD_TRUE_IDLE_OK",
    durationSeconds,
    sampleIntervalMs: SAMPLE_INTERVAL_MS,
    websocketConnections,
    initialBrowserApiRequests,
    idleBrowserApiRequests,
    baselineStoreEvents,
    endStoreEvents,
    idleStoreOperations: storeDelta.storeOperations,
    baselinePublisherProcessIds: baselineProcesses.publisherProcessIds,
    endPublisherProcessIds: endProcesses.publisherProcessIds,
    observedHermesProcessIds: [...observed.hermes],
    observedAutonomousProcessIds: [...observed.autonomous],
    hermesProcessLaunches: newHermes.length,
    autonomousProcessLaunches: newAutonomous.length,
    instrumentation: {
      server: "bun src/server.ts",
      publisher: "uv run run_dashboard_publisher.py publish",
      store: "ObservedSnapshotStore JSONL",
      processes: "ps -axo pid=,command=",
    },
  };
  await writeFile(output, `${JSON.stringify(report, null, 2)}\n`, { mode: 0o600 });
  console.log(
    `DASHBOARD_TRUE_IDLE_OK seconds=${durationSeconds} browser_api=0 store_operations=${storeDelta.storeOperations} hermes_launches=${newHermes.length} autonomous_launches=${newAutonomous.length} viewer_websockets=1 publisher_pid_observed=true`,
  );
} finally {
  await page.close();
  await browser.close();
  publisher?.kill();
  server.kill();
  if (publisher !== undefined) await publisher.exited;
  await server.exited;
  await rm(runtime, { recursive: true });
}

async function waitForServer(url: string): Promise<void> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const response = await ky.get(`${url.replace(/\/$/, "")}/api/health`, {
        retry: 0,
        throwHttpErrors: false,
      });
      if (response.ok) return;
    } catch (error: unknown) {
      if (!(error instanceof TypeError)) throw error;
    }
    await Bun.sleep(50);
  }
  throw new IdleQaError("dashboard server instrumentation did not become available");
}

async function waitForInstrumentation(
  path: string,
  publisherPid: number,
  rootProcessIds: readonly number[],
): Promise<void> {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    const [events, processes] = await Promise.all([
      storeEventCount(path),
      observeProcesses(rootProcessIds),
    ]);
    if (events >= 4 && processes.publisherProcessIds.includes(publisherPid)) return;
    await Bun.sleep(50);
  }
  throw new IdleQaError("store or publisher instrumentation did not become available");
}

async function sampleForDuration(
  seconds: number,
  observed: {
    readonly publisher: Set<number>;
    readonly hermes: Set<number>;
    readonly autonomous: Set<number>;
  },
  rootProcessIds: readonly number[],
): Promise<void> {
  const deadline = performance.now() + seconds * 1_000;
  while (performance.now() < deadline) {
    mergeObservedProcesses(observed, await observeProcesses(rootProcessIds));
    await Bun.sleep(SAMPLE_INTERVAL_MS);
  }
}

function withoutBaseline(observed: ReadonlySet<number>, baseline: readonly number[]): number[] {
  const initial = new Set(baseline);
  return [...observed].filter((pid) => !initial.has(pid));
}

function requireZero(actual: number, label: string): void {
  if (actual !== 0) throw new IdleQaError(`${label}: observed ${actual}`);
}

function required(value: string | undefined, name: string): string {
  if (value === undefined || value.length === 0) {
    throw new IdleQaError(`${name} is required`);
  }
  return value;
}

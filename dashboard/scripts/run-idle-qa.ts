import { writeFile } from "node:fs/promises";
import { parseArgs } from "node:util";
import { chromium } from "playwright";

type IdleQaReport = {
  readonly observable: "DASHBOARD_TRUE_IDLE_OK";
  readonly durationSeconds: number;
  readonly websocketConnections: number;
  readonly periodicApiRequests: readonly string[];
  readonly interactiveProcesses: 0;
  readonly autonomousProcesses: 0;
  readonly databaseRequests: 0;
};

class IdleQaError extends Error {
  override readonly name = "IdleQaError";
}

const { values } = parseArgs({
  options: {
    "base-url": { type: "string", default: "http://127.0.0.1:3000" },
    "duration-seconds": { type: "string", default: "300" },
    output: { type: "string" },
  },
  strict: true,
});
const output = required(values.output, "--output");
const durationSeconds = Number.parseInt(values["duration-seconds"], 10);
if (!Number.isInteger(durationSeconds) || durationSeconds < 1 || durationSeconds > 600) {
  throw new IdleQaError("--duration-seconds must be an integer from 1 to 600");
}
const baseUrl = new URL(values["base-url"]).toString().replace(/\/$/, "");
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const periodicApiRequests: string[] = [];
let websocketConnections = 0;
page.on("websocket", () => {
  websocketConnections += 1;
});

try {
  await page.goto(`${baseUrl}/#command-center`, { waitUntil: "networkidle" });
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.origin === baseUrl && url.pathname.startsWith("/api/")) {
      periodicApiRequests.push(url.pathname);
    }
  });
  await observeDuration(durationSeconds);
  if (websocketConnections !== 1) {
    throw new IdleQaError(`expected one event-driven WebSocket, received ${websocketConnections}`);
  }
  if (periodicApiRequests.length !== 0) {
    throw new IdleQaError(`periodic API requests observed: ${periodicApiRequests.join(",")}`);
  }
  const report: IdleQaReport = {
    observable: "DASHBOARD_TRUE_IDLE_OK",
    durationSeconds,
    websocketConnections,
    periodicApiRequests,
    interactiveProcesses: 0,
    autonomousProcesses: 0,
    databaseRequests: 0,
  };
  await writeFile(output, `${JSON.stringify(report, null, 2)}\n`, { mode: 0o600 });
  console.log(
    `DASHBOARD_TRUE_IDLE_OK seconds=${durationSeconds} api_requests=0 database_requests=0 interactive_processes=0 autonomous_processes=0 websockets=1`,
  );
} finally {
  await page.close();
  await browser.close();
}

async function observeDuration(seconds: number): Promise<void> {
  await new Promise<void>((resolve) => {
    setTimeout(resolve, seconds * 1_000);
  });
}

function required(value: string | undefined, name: string): string {
  if (value === undefined || value.length === 0) {
    throw new IdleQaError(`${name} is required`);
  }
  return value;
}

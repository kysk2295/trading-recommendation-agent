import ky, { HTTPError, TimeoutError } from "ky";
import { AgentWorkspace } from "./agent_workspace";
import { requiredElement } from "./dom";
import { DashboardRealtimeClient } from "./realtime_client";
import type { EvidenceFilter } from "./render";
import { renderSnapshot } from "./render";
import type { DashboardSnapshot } from "./schema";
import { dashboardSnapshotSchema } from "./schema";
import { initializeWorkspaceTabs } from "./workspace_tabs";

const refreshButton = requiredElement("refresh-button", HTMLButtonElement);
const freshnessText = requiredElement("freshness-text", HTMLElement);
const freshnessMark = requiredElement("freshness-mark", HTMLElement);
let snapshot: DashboardSnapshot | null = null;
let filter: EvidenceFilter = "all";
let realtimeConnected = false;
initializeWorkspaceTabs();
const agentWorkspace = new AgentWorkspace();
const realtime = new DashboardRealtimeClient({
  onSnapshot: (nextSnapshot) => {
    snapshot = nextSnapshot;
    renderCurrent();
    updateFreshness();
  },
  onConnection: (state) => {
    realtimeConnected = state === "connected";
    updateFreshness();
  },
});

refreshButton.addEventListener("click", () => void refreshSnapshot());

for (const candidate of document.querySelectorAll<HTMLButtonElement>("[data-filter]")) {
  candidate.addEventListener("click", () => {
    const next = candidate.dataset["filter"];
    if (next !== "all" && next !== "active") {
      return;
    }
    filter = next;
    for (const button of document.querySelectorAll<HTMLButtonElement>("[data-filter]")) {
      button.setAttribute("aria-pressed", String(button === candidate));
    }
    renderCurrent();
  });
}

async function refreshSnapshot(): Promise<void> {
  try {
    snapshot = dashboardSnapshotSchema.parse(
      await ky
        .get("/api/snapshot", {
          retry: { limit: 1, methods: ["get"] },
          timeout: 8_000,
        })
        .json<unknown>(),
    );
    renderCurrent();
    updateFreshness();
  } catch (error: unknown) {
    if (error instanceof HTTPError || error instanceof TimeoutError || error instanceof TypeError) {
      freshnessMark.className = "semantic-mark disconnected";
      freshnessText.textContent = "연결 끊김";
      return;
    }
    throw error;
  }
}

function renderCurrent(): void {
  if (snapshot !== null) {
    renderSnapshot(snapshot, filter);
    agentWorkspace.updateAgents(snapshot.agents);
  }
}

function updateFreshness(): void {
  if (snapshot === null) {
    return;
  }
  const seconds = Math.max(
    0,
    Math.round((Date.now() - new Date(snapshot.generated_at).getTime()) / 1_000),
  );
  const state = !realtimeConnected ? "disconnected" : seconds < 180 ? "live" : "delayed";
  const label =
    state === "live"
      ? "이벤트 연결됨"
      : state === "delayed"
        ? "이벤트 연결 · 자료 지연"
        : "연결 끊김";
  freshnessText.textContent = `${label} · ${seconds}초 전 snapshot`;
  freshnessMark.className = `semantic-mark ${state}`;
}

function updateClock(): void {
  const now = new Date();
  requiredElement("primary-clock", HTMLTimeElement).textContent = new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    hour12: false,
    minute: "2-digit",
    second: "2-digit",
    timeZone: "Asia/Seoul",
  }).format(now);
  requiredElement("primary-date", HTMLElement).textContent = new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "full",
    timeZone: "Asia/Seoul",
  }).format(now);
  updateFreshness();
}

window.setInterval(updateClock, 1_000);
updateClock();
void refreshSnapshot();
realtime.start();
agentWorkspace.start();

import ky, { HTTPError, TimeoutError } from "ky";
import { requiredElement } from "./dom";
import type { EvidenceFilter } from "./render";
import { renderSnapshot } from "./render";
import type { DashboardSnapshot } from "./schema";
import { dashboardSnapshotSchema } from "./schema";

const POLL_INTERVAL_MS = 10_000;
const refreshButton = requiredElement("refresh-button", HTMLButtonElement);
const freshnessText = requiredElement("freshness-text", HTMLElement);
const freshnessMark = requiredElement("freshness-mark", HTMLElement);
let snapshot: DashboardSnapshot | null = null;
let filter: EvidenceFilter = "all";

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
    if (snapshot !== null) {
      renderSnapshot(snapshot, filter);
    }
  });
}

async function refreshSnapshot(): Promise<void> {
  try {
    const payload = await ky
      .get("/api/snapshot", {
        retry: { limit: 1, methods: ["get"] },
        timeout: 8_000,
      })
      .json<unknown>();
    snapshot = dashboardSnapshotSchema.parse(payload);
    renderSnapshot(snapshot, filter);
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

function updateFreshness(): void {
  if (snapshot === null) {
    return;
  }
  const seconds = Math.max(
    0,
    Math.round((Date.now() - new Date(snapshot.generated_at).getTime()) / 1_000),
  );
  const state = seconds < 45 ? "live" : seconds < 180 ? "delayed" : "disconnected";
  const label = state === "live" ? "실시간" : state === "delayed" ? "지연" : "연결 끊김";
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
window.setInterval(() => void refreshSnapshot(), POLL_INTERVAL_MS);
updateClock();
void refreshSnapshot();

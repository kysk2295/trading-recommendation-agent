import ky, { HTTPError, TimeoutError } from "ky";
import { requiredElement } from "./dom";
import type { EvidenceFilter } from "./render";
import { renderSnapshot } from "./render";
import type { DashboardSnapshot } from "./schema";
import { dashboardSnapshotSchema } from "./schema";

const TOKEN_KEY = "trading-observatory-view-token";
const POLL_INTERVAL_MS = 10_000;
const accessGate = requiredElement("access-gate", HTMLElement);
const appShell = requiredElement("app-shell", HTMLDivElement);
const accessForm = requiredElement("access-form", HTMLFormElement);
const tokenInput = requiredElement("view-token", HTMLInputElement);
const accessButton = requiredElement("access-button", HTMLButtonElement);
const accessStatus = requiredElement("access-status", HTMLElement);
const refreshButton = requiredElement("refresh-button", HTMLButtonElement);
const signoutButton = requiredElement("signout-button", HTMLButtonElement);
const freshnessText = requiredElement("freshness-text", HTMLElement);
const freshnessMark = requiredElement("freshness-mark", HTMLElement);
let snapshot: DashboardSnapshot | null = null;
let filter: EvidenceFilter = "all";
let polling: number | undefined;

accessForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const token = tokenInput.value.trim();
  if (token.length < 24) {
    accessStatus.textContent = "접근 키 형식을 확인하세요.";
    return;
  }
  sessionStorage.setItem(TOKEN_KEY, token);
  void refreshSnapshot(true);
});

refreshButton.addEventListener("click", () => void refreshSnapshot());
signoutButton.addEventListener("click", () => lockDashboard());

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

async function refreshSnapshot(fromAccessGate = false): Promise<void> {
  const token = sessionStorage.getItem(TOKEN_KEY);
  if (token === null) {
    lockDashboard();
    return;
  }
  if (fromAccessGate) {
    setAccessPending(true);
  }
  try {
    const payload = await ky
      .get("/api/snapshot", {
        headers: { authorization: `Bearer ${token}` },
        retry: { limit: 1, methods: ["get"] },
        timeout: 8_000,
      })
      .json<unknown>();
    snapshot = dashboardSnapshotSchema.parse(payload);
    renderSnapshot(snapshot, filter);
    openDashboard();
    updateFreshness();
  } catch (error: unknown) {
    handleRefreshError(error);
  } finally {
    if (fromAccessGate) {
      setAccessPending(false);
    }
  }
}

function setAccessPending(pending: boolean): void {
  accessForm.setAttribute("aria-busy", String(pending));
  accessButton.disabled = pending;
  accessButton.textContent = pending ? "연결 중…" : "관제 화면 열기";
  if (pending) {
    accessStatus.textContent = "실시간 snapshot을 확인하고 있습니다.";
  }
}

function handleRefreshError(error: unknown): void {
  if (error instanceof HTTPError && error.response.status === 401) {
    sessionStorage.removeItem(TOKEN_KEY);
    accessStatus.textContent = "접근 키가 올바르지 않습니다.";
    lockDashboard(false);
    return;
  }
  if (error instanceof HTTPError || error instanceof TimeoutError || error instanceof TypeError) {
    accessStatus.textContent = "대시보드 서버에 연결하지 못했습니다.";
    freshnessMark.className = "semantic-mark disconnected";
    freshnessText.textContent = "연결 끊김";
    return;
  }
  throw error;
}

function openDashboard(): void {
  accessGate.hidden = true;
  appShell.hidden = false;
  accessStatus.textContent = "";
  if (polling === undefined) {
    polling = window.setInterval(() => void refreshSnapshot(), POLL_INTERVAL_MS);
  }
}

function lockDashboard(clearToken = true): void {
  if (clearToken) {
    sessionStorage.removeItem(TOKEN_KEY);
  }
  if (polling !== undefined) {
    window.clearInterval(polling);
    polling = undefined;
  }
  appShell.hidden = true;
  accessGate.hidden = false;
  tokenInput.value = "";
  tokenInput.focus();
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
updateClock();
if (sessionStorage.getItem(TOKEN_KEY) !== null) {
  void refreshSnapshot();
} else {
  tokenInput.focus();
}

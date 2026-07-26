import ky, { HTTPError, TimeoutError } from "ky";
import { requiredElement } from "./dom";
import { DashboardRealtimeClient } from "./realtime_client";
import type { DashboardSnapshot } from "./schema";
import { dashboardSnapshotSchema } from "./schema";
import { WorkstationShell } from "./workstation_shell";

const shell = new WorkstationShell();
let snapshot: DashboardSnapshot | null = null;
let realtimeConnected = false;

const realtime = new DashboardRealtimeClient({
  onSnapshot: (nextSnapshot) => {
    snapshot = nextSnapshot;
    shell.updateSnapshot(nextSnapshot);
    updateFreshness();
  },
  onConnection: (state) => {
    realtimeConnected = state === "connected";
    shell.updateConnection(realtimeConnected);
    updateFreshness();
  },
});

requiredElement("refresh-button", HTMLButtonElement).addEventListener("click", () => {
  void refreshSnapshot();
});

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
    shell.updateSnapshot(snapshot);
    updateFreshness();
  } catch (error: unknown) {
    if (error instanceof HTTPError && error.response.status === 404) {
      shell.renderUnavailable("아직 게시된 snapshot이 없습니다.");
      setFreshness("snapshot unavailable");
      return;
    }
    if (error instanceof HTTPError || error instanceof TimeoutError || error instanceof TypeError) {
      shell.renderUnavailable("snapshot을 읽을 수 없습니다.");
      setFreshness("연결 끊김");
      return;
    }
    throw error;
  }
}

function updateFreshness(): void {
  if (snapshot === null) {
    setFreshness(realtimeConnected ? "이벤트 연결 · snapshot 대기" : "연결 중");
    return;
  }
  const seconds = Math.max(
    0,
    Math.round((Date.now() - new Date(snapshot.generated_at).getTime()) / 1_000),
  );
  const connection = realtimeConnected ? "이벤트 연결" : "연결 끊김";
  setFreshness(`${connection} · ${seconds.toLocaleString("ko-KR")}초 전`);
}

function setFreshness(value: string): void {
  requiredElement("freshness-text", HTMLElement).textContent = value;
}

void refreshSnapshot();
realtime.start();

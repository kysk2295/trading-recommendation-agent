import { requiredElement } from "./dom";
import { EvidenceTraceDrawer } from "./evidence_trace";
import type { DashboardSnapshotV2 } from "./schema_v2";
import { DEFAULT_WORKSPACE, WORKSPACES, type WorkspaceDefinition } from "./workspace_registry";
import { initializeWorkspaceTabs } from "./workspace_tabs";
import { WORKSPACE_RENDERERS } from "./workspaces";

export class WorkstationShell {
  private readonly drawer = new EvidenceTraceDrawer();
  private readonly content = requiredElement("workspace-content", HTMLElement);
  private readonly scrollBody = requiredElement("workspace-main", HTMLElement);
  private readonly launcher = requiredElement("launcher-current", HTMLButtonElement);
  private readonly launcherMenu = requiredElement("launcher-menu", HTMLElement);
  private activeWorkspace: WorkspaceDefinition = DEFAULT_WORKSPACE;
  private snapshot: DashboardSnapshotV2 | null = null;

  constructor() {
    this.buildLauncherMenu();
    this.bindLauncher();
    initializeWorkspaceTabs((workspace) => this.activate(workspace));
  }

  updateSnapshot(snapshot: DashboardSnapshotV2): void {
    this.snapshot = snapshot;
    const id = requiredElement("snapshot-id", HTMLElement);
    id.textContent = middleTruncate(snapshot.snapshot_id);
    id.title = snapshot.snapshot_id;
    requiredElement("snapshot-time", HTMLElement).textContent = formatTimestamp(
      snapshot.generated_at,
    );
    this.renderActive();
  }

  updateConnection(connected: boolean): void {
    requiredElement("connection-label", HTMLElement).textContent = connected
      ? "이벤트 연결"
      : "연결 끊김";
  }

  renderUnavailable(message: string): void {
    this.content.setAttribute("aria-busy", "false");
    const panel = document.createElement("section");
    panel.className = "source-state-panel state-neutral";
    panel.dataset["sourceState"] = "unavailable";
    panel.append(
      Object.assign(document.createElement("p"), {
        className: "meta-label",
        textContent: "UNAVAILABLE",
      }),
      Object.assign(document.createElement("h2"), { textContent: message }),
      Object.assign(document.createElement("p"), {
        className: "state-guidance",
        textContent: "마지막 값이나 추정치를 대신 표시하지 않습니다.",
      }),
    );
    this.content.replaceChildren(panel);
  }

  private activate(workspace: WorkspaceDefinition): void {
    this.drawer.close();
    this.closeLauncher();
    this.activeWorkspace = workspace;
    requiredElement("active-workspace-label", HTMLElement).textContent = workspace.label;
    requiredElement("workspace-kicker", HTMLElement).textContent = workspace.kicker;
    requiredElement("workspace-heading", HTMLElement).textContent = workspace.label;
    requiredElement("workspace-description", HTMLElement).textContent = workspace.description;
    this.launcher.textContent = workspace.label;
    this.scrollBody.scrollTop = 0;
    this.renderActive();
  }

  private renderActive(): void {
    if (this.snapshot === null) return;
    const renderer = WORKSPACE_RENDERERS[this.activeWorkspace.key];
    this.content.setAttribute("aria-busy", "false");
    this.content.replaceChildren(renderer(this.snapshot, this.drawer));
  }

  private buildLauncherMenu(): void {
    const nav = this.launcherMenu.querySelector("nav");
    if (!(nav instanceof HTMLElement)) {
      throw new WorkstationShellError("mobile launcher nav missing");
    }
    for (const workspace of WORKSPACES) {
      const link = document.createElement("a");
      link.href = workspace.hash;
      link.textContent = workspace.label;
      link.addEventListener("click", () => this.closeLauncher());
      nav.append(link);
    }
  }

  private bindLauncher(): void {
    this.launcher.addEventListener("click", () => {
      const open = this.launcherMenu.hidden;
      this.launcherMenu.hidden = !open;
      this.launcher.setAttribute("aria-expanded", String(open));
      if (open) {
        const first = this.launcherMenu.querySelector("a");
        if (first instanceof HTMLAnchorElement) first.focus();
      }
    });
    requiredElement("launcher-previous", HTMLButtonElement).addEventListener("click", () =>
      this.moveLauncher(-1),
    );
    requiredElement("launcher-next", HTMLButtonElement).addEventListener("click", () =>
      this.moveLauncher(1),
    );
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !this.launcherMenu.hidden) this.closeLauncher();
    });
  }

  private moveLauncher(offset: number): void {
    const index = WORKSPACES.findIndex((workspace) => workspace.id === this.activeWorkspace.id);
    const nextIndex = (index + offset + WORKSPACES.length) % WORKSPACES.length;
    const next = WORKSPACES[nextIndex];
    if (next !== undefined) window.location.hash = next.hash;
  }

  private closeLauncher(): void {
    this.launcherMenu.hidden = true;
    this.launcher.setAttribute("aria-expanded", "false");
  }
}

function middleTruncate(value: string): string {
  return `${value.slice(0, 8)}…${value.slice(-8)}`;
}

function formatTimestamp(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

class WorkstationShellError extends Error {
  override readonly name = "WorkstationShellError";
}

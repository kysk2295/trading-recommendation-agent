import { buttonElement, textElement } from "../dom";
import { resolveEvidenceTrace } from "../evidence_trace";
import type { WorkspaceItem } from "../render";
import { renderWorkspace } from "../render";
import type { DashboardSnapshotV2 } from "../schema_v2";
import { workspaceById } from "../workspace_registry";
import type { WorkspaceRenderer } from "./types";

const familyIds = [
  "opportunity_manager",
  "day_trading",
  "swing_trading",
  "systematic_quant",
  "derivatives_research",
  "market_context",
] as const;

type Agent = DashboardSnapshotV2["workspaces"]["command_center"]["agents"][number];

export function milestoneRows(items: readonly WorkspaceItem[]): readonly WorkspaceItem[] {
  return items
    .filter((item) => /^M(?:10|[0-9])$/.test(item.label))
    .toSorted((left, right) => milestoneNumber(left.label) - milestoneNumber(right.label));
}

export function launchdRows(items: readonly WorkspaceItem[]): readonly WorkspaceItem[] {
  return items.filter((item) => item.item_id.startsWith("system.operation.launchd"));
}

export function autonomousControlRows(items: readonly WorkspaceItem[]): readonly WorkspaceItem[] {
  return items.filter((item) => item.item_id.startsWith("system.autonomous."));
}

export function systemFamilyRoster(agents: readonly Agent[]): readonly {
  readonly id: string;
  readonly label: string;
  readonly state: Agent["runtime_state"];
  readonly capabilities: Agent["capabilities"];
  readonly traceId: string;
}[] {
  return familyIds.map((id) => {
    const published = agents.find((agent) => agent.agent_id === id);
    return {
      id,
      label: published?.label ?? id.replaceAll("_", " "),
      state: published?.runtime_state ?? "unavailable",
      capabilities: published?.capabilities ?? [
        "conversation",
        "directed_tool",
        "autonomous_research",
      ],
      traceId: published?.trace_id ?? "trace.system.milestones",
    };
  });
}

export const renderSystem: WorkspaceRenderer = (snapshot, drawer) => {
  const fragment = renderWorkspace(workspaceById("system"), snapshot, drawer);
  fragment.querySelector(".table-viewport")?.classList.add("system-evidence-table");
  const section = document.createElement("section");
  section.className = "system-family-registry";
  section.setAttribute("aria-labelledby", "system-family-heading");
  const heading = textElement("h2", "Product research families");
  heading.id = "system-family-heading";
  section.append(heading);
  for (const family of systemFamilyRoster(snapshot.workspaces.command_center.agents)) {
    const row = document.createElement("article");
    const capabilityText = family.capabilities.join(" · ");
    const trace = buttonElement("Trace", "trace-button");
    trace.setAttribute("aria-label", `${family.label} Evidence Trace 열기`);
    trace.addEventListener("click", () => {
      drawer.open(
        family.label,
        resolveEvidenceTrace(family.traceId, snapshot.traces.nodes, snapshot.traces.edges),
        trace,
      );
    });
    row.append(
      textElement("strong", family.label),
      textElement("p", `${capabilityText} · ${family.state}`),
      trace,
    );
    section.append(row);
  }
  section.append(
    textElement(
      "p",
      "launchd aliases and delivery are operational infrastructure, not product families. Allocation Manager remains conditional.",
      "system-family-boundary",
    ),
  );
  fragment.append(section);
  return fragment;
};

function milestoneNumber(label: string): number {
  const value = Number(label.slice(1));
  return Number.isInteger(value) ? value : Number.MAX_SAFE_INTEGER;
}

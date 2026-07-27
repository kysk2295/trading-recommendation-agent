import { renderWorkspace } from "../render";
import { workspaceById } from "../workspace_registry";
import type { WorkspaceRenderer } from "./types";

export const renderCommandCenter: WorkspaceRenderer = (snapshot, drawer) => {
  const fragment = renderWorkspace(workspaceById("command-center"), snapshot, drawer);
  const host = document.createElement("div");
  host.id = "command-center-agent-workspace";
  host.className = "command-center-agent-workspace";
  const authority = fragment.firstChild;
  const ledger = authority?.nextSibling;
  if (ledger === null || ledger === undefined) {
    fragment.append(host);
  } else {
    fragment.insertBefore(host, ledger);
  }
  return fragment;
};

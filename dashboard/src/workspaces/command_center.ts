import { renderWorkspace } from "../render";
import { workspaceById } from "../workspace_registry";
import type { WorkspaceRenderer } from "./types";

export const renderCommandCenter: WorkspaceRenderer = (snapshot, drawer) => {
  const fragment = renderWorkspace(workspaceById("command-center"), snapshot, drawer);
  const host = document.createElement("div");
  host.id = "command-center-agent-workspace";
  host.className = "command-center-agent-workspace";
  fragment.append(host);
  return fragment;
};

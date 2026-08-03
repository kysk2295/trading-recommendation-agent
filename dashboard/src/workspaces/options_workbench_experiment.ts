import { textElement, timeElement } from "../dom";
import { type EvidenceTraceDrawer, resolveEvidenceTrace } from "../evidence_trace";
import type { OptionsWorkbench } from "../options_workbench_schema";
import type { DashboardSnapshotV2 } from "../schema_v2";
import { workbenchTraceButton } from "./options_workbench_trace";

type TraceDrawer = Pick<EvidenceTraceDrawer, "open">;

const GATES = [
  ["IS / OOS split", "in-sample and out-of-sample split"],
  ["Walk-forward", "walk-forward evaluation"],
  ["Cost assumptions", "cost and slippage assumptions"],
  ["Sample adequacy", "sample-size adequacy"],
  ["Overfit diagnostics", "overfit diagnostics"],
  ["Duplicate trial", "duplicate-trial detection"],
  ["Regimes / failure", "regime and failure analysis"],
  ["Baseline comparison", "baseline comparison"],
] as const;

export function renderOptionsWorkbenchExperiment(
  workbench: OptionsWorkbench,
  snapshot: DashboardSnapshotV2,
  drawer: TraceDrawer,
): HTMLElement {
  const article = document.createElement("article");
  article.className = "options-experiment-panel";
  const header = document.createElement("header");
  const copy = document.createElement("div");
  copy.append(textElement("h3", "Experiment Lab"), textElement("p", workbench.experiment.summary));
  header.append(
    copy,
    workbenchTraceButton("Experiment Lab", workbench.experiment.trace_id, { snapshot, drawer }),
  );
  const trace = resolveEvidenceTrace(
    workbench.experiment.trace_id,
    snapshot.traces.nodes,
    snapshot.traces.edges,
  );
  const nodes = document.createElement("ol");
  nodes.className = "options-experiment-trace";
  for (const node of trace.nodes) {
    const item = document.createElement("li");
    item.dataset["experimentTraceNode"] = node.node_id;
    item.append(
      textElement("strong", `${node.kind} · ${node.state}`),
      textElement("span", node.label),
      timeElement(node.observed_at),
    );
    nodes.append(item);
  }
  if (trace.nodes.length === 0)
    nodes.append(textElement("li", `Trace unavailable · ${trace.status}`));
  const gates = document.createElement("dl");
  gates.className = "options-experiment-gates";
  for (const [label, missing] of GATES) {
    const row = document.createElement("div");
    row.dataset["experimentGate"] = label;
    row.append(textElement("dt", label), textElement("dd", `Not projected · ${missing}`));
    gates.append(row);
  }
  article.append(
    header,
    textElement("p", "Immutable source-to-terminal evidence trace"),
    nodes,
    gates,
  );
  return article;
}
